"""Validação assinada e cópia de promoção SQLite para ambientes temporários.

Este módulo não ativa bancos nem altera o Flask. Ele só aceita arquivos e
diretórios privados abaixo de tempfile.gettempdir(). A aprovação é uma
assinatura RSA/SHA-256 destacada; a chave privada pertence ao processo externo de
aprovação e nunca é lida por este módulo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import migrar_financas_sqlite as migracao
import persistencia_sqlite as persistencia


FORMATO_MANIFESTO = "unirpix-sqlite-promotion"
VERSAO_MANIFESTO = 1
VERSAO_PROCEDIMENTO_PROMOCAO = 2
VALIDADE_MAXIMA_APROVACAO_SEGUNDOS = 24 * 60 * 60
_CAMPOS_FONTES = set(migracao.ARQUIVOS_ORIGEM)
_SIDECARS = ("-wal", "-shm")


class ErroPromocao(RuntimeError):
    """A validação ou preparação da cópia foi recusada."""


@dataclass(frozen=True)
class ConfiguracaoConfianca:
    chave_publica: Path
    key_id: str
    openssl: Path
    openssl_sha256: str
    ambiente_destino: str
    registro_uso: Path


@dataclass(frozen=True)
class StagingValidado:
    """Referências à aprovação; a promoção revalida tudo antes de copiar."""

    staging: Path
    manifesto: Path
    assinatura: Path
    fontes: tuple[tuple[str, Path], ...]
    manifesto_sha256: str
    staging_device: int
    staging_inode: int
    staging_size: int
    staging_mtime_ns: int
    sidecar_stats: tuple[tuple[str, int, int, int, int], ...]
    approval_id: str
    ambiente_destino: str
    config_id: tuple[str, str, str, str, str, str]
    input_identities: tuple[tuple[str, tuple[int, int, int, int, int]], ...]


def _json_canonico(valor: Any) -> bytes:
    return json.dumps(
        valor,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def serializar_manifesto(manifesto: Mapping[str, Any]) -> bytes:
    """Serializa o documento exatamente como deve ser assinado."""
    if not isinstance(manifesto, Mapping):
        raise ErroPromocao("Manifesto inválido.")
    try:
        return _json_canonico(dict(manifesto))
    except (TypeError, ValueError) as exc:
        raise ErroPromocao("Manifesto não pode ser serializado de forma canônica.") from exc


def _sha256_bytes(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def _configuracao_confiavel() -> ConfiguracaoConfianca:
    """Lê a raiz de confiança do ambiente de serviço, nunca do manifesto."""
    nomes = {
        "chave_publica": "UNIRPIX_PROMOTION_PUBLIC_KEY_PATH",
        "key_id": "UNIRPIX_PROMOTION_PUBLIC_KEY_ID",
        "openssl": "UNIRPIX_PROMOTION_OPENSSL_PATH",
        "openssl_sha256": "UNIRPIX_PROMOTION_OPENSSL_SHA256",
        "ambiente_destino": "UNIRPIX_PROMOTION_TARGET_ENV",
        "registro_uso": "UNIRPIX_PROMOTION_USAGE_DB",
    }
    valores = {campo: os.environ.get(env, "").strip() for campo, env in nomes.items()}
    if any(not value for value in valores.values()):
        raise ErroPromocao("Configuração confiável de promoção incompleta.")
    if not re.fullmatch(r"[0-9a-f]{64}", valores["key_id"]):
        raise ErroPromocao("Identificador confiável da chave pública inválido.")
    if not re.fullmatch(r"[0-9a-f]{64}", valores["openssl_sha256"]):
        raise ErroPromocao("Fingerprint confiável do OpenSSL inválido.")
    key_path = Path(valores["chave_publica"])
    openssl_path = Path(valores["openssl"])
    registry_path = Path(valores["registro_uso"])
    if not key_path.is_absolute() or not openssl_path.is_absolute() or not registry_path.is_absolute():
        raise ErroPromocao("Caminhos da configuração confiável devem ser absolutos.")
    if not valores["ambiente_destino"]:
        raise ErroPromocao("Ambiente de destino confiável ausente.")
    registry_resolved = registry_path.expanduser().resolve()
    if not _dentro_da_raiz_temporaria(registry_resolved):
        raise ErroPromocao("O registro de uso desta ferramenta deve permanecer em diretório temporário.")
    return ConfiguracaoConfianca(
        chave_publica=key_path,
        key_id=valores["key_id"],
        openssl=openssl_path,
        openssl_sha256=valores["openssl_sha256"],
        ambiente_destino=valores["ambiente_destino"],
        registro_uso=registry_resolved,
    )


def _config_id(config: ConfiguracaoConfianca) -> tuple[str, str, str, str, str, str]:
    return (
        str(config.chave_publica), config.key_id, str(config.openssl),
        config.openssl_sha256, config.ambiente_destino, str(config.registro_uso),
    )


def _identidade_arquivo(path: Path, nome: str, *, temporario: bool = True) -> tuple[int, int, int, int, int]:
    if temporario:
        _, info = _validar_arquivo_temporario(path, nome)
        assert info is not None
    else:
        if not path.is_absolute() or path.is_symlink():
            raise ErroPromocao(f"{nome} deve ter caminho absoluto e não ser link simbólico.")
        try:
            resolved = path.resolve(strict=True)
            info = resolved.stat()
        except OSError as exc:
            raise ErroPromocao(f"Não foi possível validar {nome}.") from exc
        uid = os.getuid() if hasattr(os, "getuid") else None
        if (
            not stat.S_ISREG(info.st_mode)
            or (uid is not None and info.st_uid not in {0, uid})
            or info.st_mode & 0o022
            or (not nome.lower().startswith("openssl") and info.st_nlink != 1)
        ):
            raise ErroPromocao(f"{nome} tem tipo, propriedade ou permissões inseguros.")
        if nome.lower().startswith("openssl") and not info.st_mode & 0o111:
            raise ErroPromocao("O executável OpenSSL não é executável.")
        path = resolved
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _ler_arquivo_verificado(path: Path, nome: str, *, temporario: bool = True) -> tuple[bytes, tuple[int, int, int, int, int]]:
    identidade_antes = _identidade_arquivo(path, nome, temporario=temporario)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
        try:
            info = os.fstat(fd)
            identidade_fd = (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
            )
            if identidade_fd != identidade_antes:
                raise ErroPromocao(f"{nome} mudou durante a abertura segura.")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                content = stream.read()
            info_depois = os.fstat(fd)
            identidade_depois = (
                info_depois.st_dev, info_depois.st_ino, info_depois.st_size,
                info_depois.st_mtime_ns, info_depois.st_ctime_ns,
            )
            if identidade_depois != identidade_fd:
                raise ErroPromocao(f"{nome} mudou durante a leitura.")
        finally:
            os.close(fd)
    except OSError as exc:
        raise ErroPromocao(f"Não foi possível ler {nome} com segurança.") from exc
    if _identidade_arquivo(path, nome, temporario=temporario) != identidade_antes:
        raise ErroPromocao(f"{nome} foi substituído durante a leitura.")
    return content, identidade_antes


def _sha256_arquivo(caminho: Path) -> str:
    return _sha256_bytes(migracao._ler_copia_temporaria(caminho))


def _dentro_da_raiz_temporaria(caminho: Path) -> bool:
    raiz = Path(tempfile.gettempdir()).resolve()
    try:
        return os.path.commonpath((str(caminho), str(raiz))) == str(raiz)
    except ValueError:
        return False


def _validar_arquivo_temporario(
    caminho: str | os.PathLike[str], nome: str, *, existir: bool = True
) -> tuple[Path, os.stat_result | None]:
    entrada = Path(caminho).expanduser()
    if not entrada.is_absolute():
        raise ErroPromocao(f"{nome} precisa usar caminho absoluto.")
    if entrada.is_symlink():
        raise ErroPromocao(f"{nome} não pode ser link simbólico.")
    try:
        resolvido = entrada.resolve(strict=existir)
        if not _dentro_da_raiz_temporaria(resolvido):
            raise ErroPromocao(f"{nome} deve estar em diretório temporário privado.")
        migracao._validar_diretorios_temporarios_privados(resolvido)
        if not existir:
            if resolvido.exists():
                raise ErroPromocao(f"{nome} já existe; sobrescrita não permitida.")
            return resolvido, None
        info = resolvido.stat()
    except (OSError, RuntimeError) as exc:
        raise ErroPromocao(f"Não foi possível validar {nome}.") from exc
    uid = os.getuid() if hasattr(os, "getuid") else None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (uid is not None and info.st_uid != uid)
        or info.st_mode & 0o022
    ):
        raise ErroPromocao(f"{nome} precisa ser arquivo regular, privado e sem hard links.")
    return resolvido, info


def _validar_diretorio_destino(caminho: Path) -> Path:
    if not caminho.is_absolute():
        raise ErroPromocao("O destino precisa usar caminho absoluto.")
    if caminho.is_symlink():
        raise ErroPromocao("O destino não pode ser link simbólico.")
    try:
        pai = caminho.parent.resolve(strict=True)
        if not _dentro_da_raiz_temporaria(pai):
            raise ErroPromocao("O destino precisa estar em diretório temporário privado.")
        migracao._validar_diretorios_temporarios_privados(pai / "novo.sqlite3")
        info = pai.stat()
        alvo = pai / caminho.name
        if alvo.exists() or alvo.is_symlink():
            raise ErroPromocao("O destino já existe; sobrescrita não permitida.")
        if any(Path(str(alvo) + sufixo).exists() or Path(str(alvo) + sufixo).is_symlink() for sufixo in _SIDECARS):
            raise ErroPromocao("Há sidecars preexistentes para o destino reservado.")
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ErroPromocao("O diretório de destino não é privado.")
        uid = os.getuid() if hasattr(os, "getuid") else None
        if uid is not None and info.st_uid != uid:
            raise ErroPromocao("O diretório de destino não pertence ao usuário atual.")
        return alvo
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, ErroPromocao):
            raise
        raise ErroPromocao("Não foi possível validar o diretório de destino.") from exc


def inicializar_registro_uso_temporario(caminho: str | os.PathLike[str]) -> Path:
    """Cria um registro de replay/revogação novo, somente em diretório temporário."""
    path = Path(caminho).expanduser()
    target = _validar_diretorio_destino(path)
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(target), flags, 0o600)
    except FileExistsError as exc:
        raise ErroPromocao("O registro de uso já existe; não será substituído.") from exc
    created = os.fstat(fd)
    os.close(fd)
    try:
        connection = sqlite3.connect(target)
        try:
            connection.execute(
                "CREATE TABLE approvals ("
                "approval_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL, "
                "target_environment TEXT NOT NULL, state TEXT NOT NULL "
                "CHECK(state IN ('reserved','available','used','revoked')), updated_at TEXT NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()
        target.chmod(0o600)
        return target
    except BaseException as exc:
        try:
            current = target.stat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                target.unlink()
        except OSError:
            pass
        if isinstance(exc, ErroPromocao):
            raise
        raise ErroPromocao("Não foi possível inicializar o registro temporário de uso.") from exc


def _validar_registro_uso(config: ConfiguracaoConfianca) -> None:
    path = config.registro_uso
    _identidade_arquivo(path, "Registro de uso")
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            if row is None or row[0] != "ok":
                raise ErroPromocao("Registro de uso inconsistente.")
            columns = {
                item[1] for item in connection.execute("PRAGMA table_info(approvals)")
            }
            if columns != {
                "approval_id", "manifest_sha256", "target_environment", "state", "updated_at"
            }:
                raise ErroPromocao("Estrutura do registro de uso incompatível.")
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        raise ErroPromocao("Registro de uso ausente ou inválido.") from exc


def _estado_aprovacao(
    config: ConfiguracaoConfianca, approval_id: str, manifest_sha: str,
    *, permitir_reservada: bool = False,
) -> str | None:
    _validar_registro_uso(config)
    try:
        connection = sqlite3.connect(config.registro_uso.as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT manifest_sha256, target_environment, state FROM approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ErroPromocao("Falha ao consultar o registro de aprovação.") from exc
    if row is None:
        return None
    if row[0] != manifest_sha or row[1] != config.ambiente_destino:
        raise ErroPromocao("A aprovação já foi associada a outro manifesto ou ambiente.")
    if row[2] in {"used", "revoked"} or (row[2] == "reserved" and not permitir_reservada):
        raise ErroPromocao("A aprovação já foi usada, revogada ou está em processamento.")
    return str(row[2])


def _reservar_aprovacao(
    config: ConfiguracaoConfianca, approval_id: str, manifest_sha: str
) -> None:
    _validar_registro_uso(config)
    connection = sqlite3.connect(config.registro_uso, timeout=10, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT manifest_sha256, target_environment, state FROM approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if row is None:
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, 'reserved', ?)",
                (approval_id, manifest_sha, config.ambiente_destino, now),
            )
        elif (
            row[0] != manifest_sha or row[1] != config.ambiente_destino
            or row[2] != "available"
        ):
            raise ErroPromocao("A aprovação não pode ser reservada para esta promoção.")
        else:
            connection.execute(
                "UPDATE approvals SET state='reserved', updated_at=? WHERE approval_id=? AND state='available'",
                (now, approval_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ErroPromocao("A aprovação foi concorrida por outra operação.")
        connection.commit()
    except BaseException as exc:
        if connection.in_transaction:
            connection.rollback()
        if isinstance(exc, ErroPromocao):
            raise
        raise ErroPromocao("Não foi possível reservar a aprovação.") from exc
    finally:
        connection.close()


def _finalizar_aprovacao(
    config: ConfiguracaoConfianca, approval_id: str, manifest_sha: str
) -> None:
    with sqlite3.connect(config.registro_uso, timeout=10, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "UPDATE approvals SET state='used', updated_at=? "
            "WHERE approval_id=? AND manifest_sha256=? AND state='reserved'",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), approval_id, manifest_sha),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise ErroPromocao("A aprovação foi revogada ou alterada durante a cópia.")
        connection.commit()


def _liberar_reserva_aprovacao(
    config: ConfiguracaoConfianca, approval_id: str, manifest_sha: str
) -> bool:
    try:
        with sqlite3.connect(config.registro_uso, timeout=10, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE approvals SET state='available', updated_at=? "
                "WHERE approval_id=? AND manifest_sha256=? AND state='reserved'",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), approval_id, manifest_sha),
            )
            connection.commit()
            return cursor.rowcount == 1
    except sqlite3.Error:
        return False


def revogar_aprovacao_temporaria(approval_id: str, manifest_sha256: str) -> None:
    """Revoga uma aprovação apenas no registro temporário configurado."""
    config = _configuracao_confiavel()
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise ErroPromocao("Fingerprint do manifesto inválido para revogação.")
    _validar_registro_uso(config)
    with sqlite3.connect(config.registro_uso, timeout=10, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT manifest_sha256, target_environment, state FROM approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if row is not None and (row[0] != manifest_sha256 or row[1] != config.ambiente_destino):
            connection.rollback()
            raise ErroPromocao("Aprovação diverge do manifesto ou ambiente informado.")
        if row is not None and row[2] == "used":
            connection.rollback()
            raise ErroPromocao("Uma aprovação já utilizada não pode ser revogada retroativamente.")
        if row is None:
            connection.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, 'revoked', ?)",
                (approval_id, manifest_sha256, config.ambiente_destino, now),
            )
        else:
            connection.execute(
                "UPDATE approvals SET state='revoked', updated_at=? WHERE approval_id=?",
                (now, approval_id),
            )
        connection.commit()


def _conexao_somente_leitura(caminho: Path) -> sqlite3.Connection:
    _validar_sidecars(caminho)
    try:
        conexao = sqlite3.connect(caminho.as_uri() + "?mode=ro", uri=True)
        conexao.row_factory = sqlite3.Row
        conexao.execute("BEGIN")
        return conexao
    except sqlite3.Error as exc:
        raise ErroPromocao("Não foi possível abrir o banco somente para leitura.") from exc


def _validar_sidecars(caminho: Path) -> tuple[tuple[str, int, int, int, int], ...]:
    stats: list[tuple[str, int, int, int, int]] = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for sufixo in _SIDECARS:
        sidecar = Path(str(caminho) + sufixo)
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        try:
            info = sidecar.lstat()
        except OSError as exc:
            raise ErroPromocao("Não foi possível validar os arquivos auxiliares do SQLite.") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (uid is not None and info.st_uid != uid)
            or info.st_mode & 0o022
        ):
            raise ErroPromocao("Sidecars do SQLite precisam ser arquivos privados sem hard links.")
        # SQLite can update -shm lock/index bytes while a read-only connection
        # is open; only the WAL is part of the committed database snapshot.
        if sufixo == "-wal":
            stats.append((sufixo, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
    return tuple(stats)


def _hashes_banco_staging(caminho: Path) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {"sha256": _sha256_arquivo(caminho), "wal_sha256": None}
    wal = Path(str(caminho) + "-wal")
    if wal.exists():
        hashes["wal_sha256"] = _sha256_arquivo(wal)
    return hashes


def _valor_tipado(valor: Any) -> list[Any]:
    if valor is None:
        return ["null", None]
    if isinstance(valor, bool):
        return ["bool", valor]
    if isinstance(valor, int):
        return ["integer", valor]
    if isinstance(valor, float):
        return ["real", valor.hex()]
    if isinstance(valor, bytes):
        return ["blob", valor.hex()]
    if isinstance(valor, str):
        return ["text", valor]
    raise ErroPromocao("O banco contém um tipo de dado não suportado na impressão digital.")


def _fingerprint_banco(conexao: sqlite3.Connection) -> str:
    objetos = [
        [str(row["type"]), str(row["name"]), " ".join(str(row["sql"]).split())]
        for row in conexao.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type, name"
        )
    ]
    tabelas: list[dict[str, Any]] = []
    for objeto in objetos:
        if objeto[0] != "table":
            continue
        nome = objeto[1]
        identificador = '"' + nome.replace('"', '""') + '"'
        colunas = [str(row["name"]) for row in conexao.execute(f"PRAGMA table_info({identificador})")]
        linhas = [
            [_valor_tipado(valor) for valor in row]
            for row in conexao.execute(f"SELECT * FROM {identificador} ORDER BY rowid")
        ]
        tabelas.append({"name": nome, "columns": colunas, "rows": linhas})
    return _sha256_bytes(_json_canonico({"objects": objetos, "tables": tabelas}))


def _contagens_banco(conexao: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "users": "SELECT COUNT(*) FROM users",
        "accounts": "SELECT COUNT(*) FROM accounts",
        "deposits": "SELECT COUNT(*) FROM deposits",
        "bets": "SELECT COUNT(*) FROM bets",
        "legacy_deposits": "SELECT COUNT(*) FROM legacy_history WHERE history_type='deposit'",
        "legacy_bets": "SELECT COUNT(*) FROM legacy_history WHERE history_type='bet'",
        "migration_batches": "SELECT COUNT(*) FROM migration_batches",
        "idempotency_keys": "SELECT COUNT(*) FROM idempotency_keys",
        "non_opening_transactions": (
            "SELECT COUNT(*) FROM financial_transactions WHERE operation_type <> 'opening_balance'"
        ),
    }
    try:
        return {nome: int(conexao.execute(query).fetchone()[0]) for nome, query in queries.items()}
    except sqlite3.Error as exc:
        raise ErroPromocao("Não foi possível conferir as contagens do banco.") from exc


def _ler_fontes(
    fontes: Mapping[str, str | os.PathLike[str]],
) -> tuple[
    dict[str, bytes], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]], str,
]:
    if set(fontes) != _CAMPOS_FONTES:
        raise ErroPromocao("O manifesto exige exatamente as três fontes da migração.")
    conteudos: dict[str, bytes] = {}
    manifest_sources: dict[str, dict[str, Any]] = {}
    for nome in migracao.ARQUIVOS_ORIGEM:
        caminho, _ = _validar_arquivo_temporario(fontes[nome], f"Fonte {nome}")
        conteudo, _identity = _ler_arquivo_verificado(caminho, f"Fonte {nome}")
        conteudos[nome] = conteudo
        manifest_sources[nome] = {
            "sha256": _sha256_bytes(conteudo),
            "size_bytes": len(conteudo),
        }
    relatorio, registros, analise_metadata = migracao.analisar_jsons(conteudos, dry_run=True)
    if not relatorio.elegivel:
        raise ErroPromocao("As fontes não passaram na pré-validação da migração.")
    return conteudos, manifest_sources, registros, analise_metadata, migracao._fingerprint(registros)


def _validar_conteudo_staging(
    staging: Path,
    registros: Mapping[str, list[dict[str, Any]]],
    metadados: Mapping[str, list[dict[str, Any]]],
    fingerprint_lote: str,
) -> tuple[dict[str, int], str]:
    try:
        persistencia.validar_esquema_banco(staging)
        conexao = _conexao_somente_leitura(staging)
        try:
            contagens = _contagens_banco(conexao)
            fingerprint_dados = _fingerprint_banco(conexao)
            metadata = conexao.execute(
                "SELECT bundle_hash, imported_at, account_count, deposit_history_count, bet_history_count "
                "FROM migration_batches"
            ).fetchall()
            users = conexao.execute(
                "SELECT user_id, cpf, nome, senha_hash, ativo FROM users ORDER BY cpf"
            ).fetchall()
            accounts = conexao.execute(
                "SELECT user_id, balance_cents, last_transaction_id FROM accounts ORDER BY user_id"
            ).fetchall()
            openings = conexao.execute(
                "SELECT transaction_id, user_id, delta_cents, occurred_at, source_file, source_row "
                "FROM financial_transactions WHERE operation_type='opening_balance' ORDER BY source_row"
            ).fetchall()
            histories = conexao.execute(
                "SELECT bundle_hash, source_file, source_row, history_type, user_id, "
                "original_data_json, original_datetime, record_hash "
                "FROM legacy_history ORDER BY source_file, source_row"
            ).fetchall()
        finally:
            conexao.close()
    except (sqlite3.Error, persistencia.ErroPersistencia, OSError) as exc:
        if isinstance(exc, ErroPromocao):
            raise
        raise ErroPromocao("O banco de staging não passou nas verificações SQLite.") from exc

    esperadas = {
        "users": len(registros["cadastro.json"]),
        "accounts": len(registros["cadastro.json"]),
        "legacy_deposits": len(registros["depositos.json"]),
        "legacy_bets": len(registros["apostas.json"]),
    }
    if any(contagens[nome] != valor for nome, valor in esperadas.items()):
        raise ErroPromocao("As contagens do staging divergem das fontes aprovadas.")
    if (
        contagens["deposits"] or contagens["bets"]
        or contagens["non_opening_transactions"] or contagens["idempotency_keys"]
    ):
        raise ErroPromocao("Staging contém operações financeiras além dos saldos de abertura.")
    if contagens["migration_batches"] != 1 or len(metadata) != 1:
        raise ErroPromocao("Staging precisa conter exatamente um lote de migração aprovado.")
    lote = metadata[0]
    if (
        str(lote["bundle_hash"]) != fingerprint_lote
        or int(lote["account_count"]) != esperadas["users"]
        or int(lote["deposit_history_count"]) != esperadas["legacy_deposits"]
        or int(lote["bet_history_count"]) != esperadas["legacy_bets"]
    ):
        raise ErroPromocao("Fingerprint ou metadados do lote divergem das fontes aprovadas.")
    try:
        batch_time = datetime.fromisoformat(str(lote["imported_at"]))
        if batch_time.tzinfo is None:
            raise ValueError("timezone ausente")
    except (TypeError, ValueError) as exc:
        raise ErroPromocao("Data do lote de migração inválida.") from exc

    source_users: dict[str, tuple[int, str, str | None, int]] = {}
    for index, row in enumerate(registros["cadastro.json"]):
        normalized = metadados["cadastro.json"][index]["cpf_normalizado"]
        opening_cents = metadados["cadastro.json"][index]["centavos_legados"]["saldo"]
        source_users[normalized] = (
            opening_cents, row["nome"], row.get("senha_hash"), index
        )
    database_users = {
        str(row["cpf"]): (
            int(row["user_id"]), str(row["nome"]), row["senha_hash"], int(row["ativo"])
        )
        for row in users
    }
    if set(database_users) != set(source_users):
        raise ErroPromocao("As contas do staging divergem dos usuários das fontes.")
    user_ids: dict[str, int] = {}
    for cpf, (expected_balance, expected_name, expected_hash, _source_row) in source_users.items():
        user_id, name, password_hash, active = database_users[cpf]
        if (name, password_hash) != (expected_name, expected_hash) or active != 1:
            raise ErroPromocao("Os dados de usuário do staging divergem das fontes aprovadas.")
        user_ids[cpf] = user_id

    account_by_user = {
        int(row["user_id"]): (int(row["balance_cents"]), int(row["last_transaction_id"]))
        for row in accounts
    }
    opening_by_user = {int(row["user_id"]): row for row in openings}
    if set(account_by_user) != set(user_ids.values()) or set(opening_by_user) != set(user_ids.values()):
        raise ErroPromocao("Contas e saldos de abertura do staging estão incompletos.")
    for cpf, (expected_balance, _name, _password_hash, source_row) in source_users.items():
        user_id = user_ids[cpf]
        balance_cents, last_transaction_id = account_by_user[user_id]
        opening = opening_by_user[user_id]
        try:
            opening_time = datetime.fromisoformat(str(opening["occurred_at"]))
            if opening_time.tzinfo is None:
                raise ValueError("timezone ausente")
        except (TypeError, ValueError) as exc:
            raise ErroPromocao("Data do saldo de abertura inválida.") from exc
        if (
            balance_cents != expected_balance
            or int(opening["delta_cents"]) != expected_balance
            or opening["source_file"] != "cadastro.json"
            or int(opening["source_row"]) != source_row
            or last_transaction_id != int(opening["transaction_id"])
        ):
            raise ErroPromocao("Saldo de abertura diverge do saldo atual aprovado.")

    expected_histories: dict[tuple[str, int], tuple[str, str, str, str, str | None]] = {}
    for filename, history_type in (("depositos.json", "deposit"), ("apostas.json", "bet")):
        for index, row in enumerate(registros[filename]):
            canonical = migracao._canonical_json(row)
            record_hash = _sha256_bytes(f"{filename}\0{canonical}".encode("utf-8"))
            cpf = metadados[filename][index]["cpf_normalizado"]
            expected_histories[(filename, index)] = (
                history_type,
                cpf,
                record_hash,
                canonical,
                migracao._timestamp_original(row),
            )
    actual_histories: dict[tuple[str, int], tuple[str, int, str, str, str | None]] = {}
    actual_bundles: dict[tuple[str, int], str] = {}
    for row in histories:
        key = (str(row["source_file"]), int(row["source_row"]))
        actual_bundles[key] = str(row["bundle_hash"])
        actual_histories[key] = (
            str(row["history_type"]),
            int(row["user_id"]),
            str(row["record_hash"]),
            str(row["original_data_json"]),
            row["original_datetime"],
        )
    if set(actual_histories) != set(expected_histories):
        raise ErroPromocao("Posições ou origens do histórico divergem das fontes.")
    for key, (kind, cpf, record_hash, canonical, timestamp) in expected_histories.items():
        actual_kind, actual_user, actual_hash, actual_json, actual_timestamp = actual_histories[key]
        if (
            actual_kind != kind
            or actual_user != user_ids[cpf]
            or actual_hash != record_hash
            or actual_json != canonical
            or actual_timestamp != timestamp
            or actual_bundles[key] != fingerprint_lote
        ):
            raise ErroPromocao("Um registro do histórico legado diverge das fontes aprovadas.")
    return contagens, fingerprint_dados


def preparar_manifesto(
    staging: str | os.PathLike[str],
    fontes: Mapping[str, str | os.PathLike[str]],
    *,
    staging_id: str | None = None,
) -> dict[str, Any]:
    """Gera manifesto pendente; não o aprova nem assina."""
    banco, info = _validar_arquivo_temporario(staging, "Banco de staging")
    assert info is not None
    conteudos, hashes_fontes, registros, metadados, fingerprint_lote = _ler_fontes(fontes)
    del conteudos
    contagens, fingerprint_dados = _validar_conteudo_staging(
        banco, registros, metadados, fingerprint_lote
    )
    esquema = persistencia.ARQUIVO_ESQUEMA.read_bytes()
    migrador_sha = _sha256_bytes(Path(migracao.__file__).read_bytes())
    return {
        "format": FORMATO_MANIFESTO,
        "manifest_version": VERSAO_MANIFESTO,
        "promotion_procedure_version": VERSAO_PROCEDIMENTO_PROMOCAO,
        "staging_id": staging_id or str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema": {
            "version": persistencia.VERSAO_ESQUEMA,
            "sha256": _sha256_bytes(esquema),
        },
        "migrator": {
            "module": Path(migracao.__file__).name,
            # Identificador de versão imutável do código, pois o migrador ainda
            # não define uma versão semântica própria.
            "version": f"sha256:{migrador_sha}",
            "source_sha256": migrador_sha,
        },
        "sources": hashes_fontes,
        "batch_fingerprint": fingerprint_lote,
        "counts": {
            "source": {
                "users": len(registros["cadastro.json"]),
                "deposits": len(registros["depositos.json"]),
                "bets": len(registros["apostas.json"]),
            },
            "staging": contagens,
        },
        "staging_database": {
            **_hashes_banco_staging(banco),
            "content_sha256": fingerprint_dados,
        },
        "approval": None,
    }


def _carregar_manifesto(caminho: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw, _identity = _ler_arquivo_verificado(caminho, "Manifesto")
        manifesto = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ErroPromocao("Manifesto ausente ou inválido.") from exc
    if not isinstance(manifesto, dict) or _json_canonico(manifesto) != raw:
        raise ErroPromocao("O manifesto não está em formato canônico válido.")
    if (
        manifesto.get("format") != FORMATO_MANIFESTO
        or manifesto.get("manifest_version") != VERSAO_MANIFESTO
        or manifesto.get("promotion_procedure_version") != VERSAO_PROCEDIMENTO_PROMOCAO
    ):
        raise ErroPromocao("Formato ou versão do manifesto incompatível.")
    approval = manifesto.get("approval")
    if (
        not isinstance(approval, dict)
        or approval.get("status") != "approved"
        or not isinstance(approval.get("approved_by"), str)
        or not approval["approved_by"].strip()
        or not isinstance(approval.get("approved_at"), str)
        or not isinstance(approval.get("expires_at"), str)
        or not isinstance(approval.get("key_id"), str)
        or not isinstance(approval.get("approval_id"), str)
        or not isinstance(approval.get("target_environment"), str)
        or approval.get("signature_algorithm") != "RSA-SHA256"
    ):
        raise ErroPromocao("Manifesto ausente de aprovação administrativa.")
    if not isinstance(manifesto.get("staging_id"), str) or not manifesto["staging_id"].strip():
        raise ErroPromocao("Identificação do staging ausente.")
    try:
        generated_at = datetime.fromisoformat(manifesto["generated_at"])
        if generated_at.tzinfo is None:
            raise ValueError("timezone ausente")
    except (KeyError, TypeError, ValueError) as exc:
        raise ErroPromocao("Data de geração do manifesto inválida.") from exc
    try:
        approval_id = str(uuid.UUID(approval["approval_id"]))
        approved_at = datetime.fromisoformat(approval["approved_at"])
        expires_at = datetime.fromisoformat(approval["expires_at"])
        generated_at = datetime.fromisoformat(manifesto["generated_at"])
        if any(item.tzinfo is None or item.utcoffset() is None for item in (approved_at, expires_at, generated_at)):
            raise ValueError("timezone ausente")
    except (KeyError, TypeError, ValueError) as exc:
        raise ErroPromocao("Identificação ou datas da aprovação são inválidas.") from exc
    now = datetime.now(timezone.utc)
    approved_utc = approved_at.astimezone(timezone.utc)
    expires_utc = expires_at.astimezone(timezone.utc)
    generated_utc = generated_at.astimezone(timezone.utc)
    if (
        approval_id != approval["approval_id"]
        or approved_utc > now
        or expires_utc <= now
        or expires_utc <= approved_utc
        or (expires_utc - approved_utc).total_seconds() > VALIDADE_MAXIMA_APROVACAO_SEGUNDOS
        or approved_utc < generated_utc
    ):
        raise ErroPromocao("A aprovação expirou ou excede a validade permitida.")
    return manifesto, raw


def _verificar_assinatura(
    raw: bytes,
    assinatura: Path,
    approval: Mapping[str, Any],
    config: ConfiguracaoConfianca,
) -> None:
    try:
        chave, chave_identity = _ler_arquivo_verificado(
            config.chave_publica, "Chave pública confiável", temporario=False
        )
        key_id = _sha256_bytes(chave)
        if key_id != config.key_id or approval.get("key_id") != config.key_id:
            raise ErroPromocao("A chave pública não corresponde à raiz de confiança configurada.")
        openssl_identity = _identidade_arquivo(config.openssl, "OpenSSL confiável", temporario=False)
        openssl_content, _ = _ler_arquivo_verificado(config.openssl, "OpenSSL confiável", temporario=False)
        if _sha256_bytes(openssl_content) != config.openssl_sha256:
            raise ErroPromocao("O executável OpenSSL diverge do fingerprint confiável.")
        key_check = subprocess.run(
            [
                str(config.openssl), "rsa", "-pubin", "-in", str(config.chave_publica),
                "-text", "-noout",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        match = re.search(rb"Public-Key:\s*\((\d+) bit\)", key_check.stdout)
        if key_check.returncode != 0 or not match:
            raise ErroPromocao("A chave pública confiável não é uma chave RSA reconhecida.")
        if int(match.group(1)) < 3072:
            raise ErroPromocao("A chave RSA confiável precisa ter no mínimo 3072 bits.")
        signature_bytes, signature_identity = _ler_arquivo_verificado(
            assinatura, "Assinatura", temporario=True
        )
        if not signature_bytes:
            raise ErroPromocao("Assinatura vazia.")
        result = subprocess.run(
            [
                str(config.openssl), "dgst", "-sha256", "-verify", str(config.chave_publica),
                "-signature", str(assinatura),
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ErroPromocao("Não foi possível verificar a assinatura RSA/SHA-256.") from exc
    if result.returncode != 0:
        raise ErroPromocao("Assinatura do manifesto inválida.")
    if (
        _identidade_arquivo(config.chave_publica, "Chave pública confiável", temporario=False)
        != chave_identity
        or _identidade_arquivo(config.openssl, "OpenSSL confiável", temporario=False)
        != openssl_identity
        or _identidade_arquivo(assinatura, "Assinatura") != signature_identity
    ):
        raise ErroPromocao("Chave, OpenSSL ou assinatura mudou durante a verificação.")


def validar_staging(
    staging: str | os.PathLike[str],
    manifesto: str | os.PathLike[str],
    assinatura: str | os.PathLike[str],
    fontes: Mapping[str, str | os.PathLike[str]],
    *,
    _permitir_reservada: bool = False,
) -> StagingValidado:
    """Valida manifesto assinado, fontes e staging sem gravar no banco."""
    config = _configuracao_confiavel()
    banco, info = _validar_arquivo_temporario(staging, "Banco de staging")
    manifest_path, _ = _validar_arquivo_temporario(manifesto, "Manifesto")
    signature_path, _ = _validar_arquivo_temporario(assinatura, "Assinatura")
    assert info is not None
    identities_before: dict[str, tuple[int, int, int, int, int]] = {
        "staging": _identidade_arquivo(banco, "Banco de staging"),
        "manifest": _identidade_arquivo(manifest_path, "Manifesto"),
        "signature": _identidade_arquivo(signature_path, "Assinatura"),
        "public_key": _identidade_arquivo(config.chave_publica, "Chave pública confiável", temporario=False),
        "openssl": _identidade_arquivo(config.openssl, "OpenSSL confiável", temporario=False),
    }
    if set(fontes) != _CAMPOS_FONTES:
        raise ErroPromocao("O manifesto exige exatamente as três fontes da migração.")
    source_paths: dict[str, Path] = {}
    for name in migracao.ARQUIVOS_ORIGEM:
        source_paths[name], _ = _validar_arquivo_temporario(fontes[name], f"Fonte {name}")
        identities_before[f"source:{name}"] = _identidade_arquivo(
            source_paths[name], f"Fonte {name}"
        )
    manifest_data, raw = _carregar_manifesto(manifest_path)
    approval = manifest_data["approval"]
    if approval.get("target_environment") != config.ambiente_destino:
        raise ErroPromocao("A aprovação não autoriza este ambiente de destino.")
    if approval.get("key_id") != config.key_id:
        raise ErroPromocao("A aprovação não corresponde à chave pública confiável configurada.")
    _verificar_assinatura(raw, signature_path, approval, config)
    manifest_sha = _sha256_bytes(raw)
    _estado_aprovacao(
        config, approval["approval_id"], manifest_sha,
        permitir_reservada=_permitir_reservada,
    )

    conteudos, hashes_fontes, registros, metadados, fingerprint_lote = _ler_fontes(fontes)
    del conteudos
    contagens, fingerprint_dados = _validar_conteudo_staging(
        banco, registros, metadados, fingerprint_lote
    )
    esquema_sha = _sha256_bytes(persistencia.ARQUIVO_ESQUEMA.read_bytes())
    migrador_sha = _sha256_bytes(Path(migracao.__file__).read_bytes())
    comparacoes = (
        manifest_data.get("promotion_procedure_version") == VERSAO_PROCEDIMENTO_PROMOCAO,
        manifest_data.get("schema") == {"version": persistencia.VERSAO_ESQUEMA, "sha256": esquema_sha},
        manifest_data.get("migrator") == {
            "module": Path(migracao.__file__).name,
            "version": f"sha256:{migrador_sha}",
            "source_sha256": migrador_sha,
        },
        manifest_data.get("sources") == hashes_fontes,
        manifest_data.get("batch_fingerprint") == fingerprint_lote,
        manifest_data.get("counts") == {
            "source": {
                "users": len(registros["cadastro.json"]),
                "deposits": len(registros["depositos.json"]),
                "bets": len(registros["apostas.json"]),
            },
            "staging": contagens,
        },
        manifest_data.get("staging_database") == {
            **_hashes_banco_staging(banco),
            "content_sha256": fingerprint_dados,
        },
    )
    if not all(comparacoes):
        raise ErroPromocao("Manifesto, fontes e banco de staging apresentam divergência.")
    sidecar_stats = _validar_sidecars(banco)
    for suffix, *_ in sidecar_stats:
        identities_before[f"staging{suffix}"] = _identidade_arquivo(
            Path(str(banco) + suffix), f"Sidecar {suffix}"
        )
    identities_after = dict(identities_before)
    current_paths = {
        "staging": banco,
        "manifest": manifest_path,
        "signature": signature_path,
        "public_key": config.chave_publica,
        "openssl": config.openssl,
        **{f"source:{name}": path for name, path in source_paths.items()},
    }
    for name, path in current_paths.items():
        identities_after[name] = _identidade_arquivo(
            path, name, temporario=name not in {"public_key", "openssl"}
        )
    for name, identity in identities_before.items():
        if name in identities_after and identities_after[name] != identity:
            raise ErroPromocao(f"{name} mudou durante a validação.")
    if _sha256_bytes(_ler_arquivo_verificado(manifest_path, "Manifesto")[0]) != manifest_sha:
        raise ErroPromocao("O manifesto mudou durante a validação.")
    _estado_aprovacao(
        config, approval["approval_id"], manifest_sha,
        permitir_reservada=_permitir_reservada,
    )
    return StagingValidado(
        staging=banco,
        manifesto=manifest_path,
        assinatura=signature_path,
        fontes=tuple((nome, source_paths[nome]) for nome in migracao.ARQUIVOS_ORIGEM),
        manifesto_sha256=manifest_sha,
        staging_device=info.st_dev,
        staging_inode=info.st_ino,
        staging_size=info.st_size,
        staging_mtime_ns=info.st_mtime_ns,
        sidecar_stats=sidecar_stats,
        approval_id=approval["approval_id"],
        ambiente_destino=config.ambiente_destino,
        config_id=_config_id(config),
        input_identities=tuple(sorted(identities_after.items())),
    )


def _executar_backup(origem: sqlite3.Connection, destino: sqlite3.Connection) -> None:
    origem.backup(destino)


def _reservar_sidecars_proprios(caminho: Path) -> dict[str, tuple[int, int]]:
    """Reserva exclusivamente os sidecars para atribuir sua propriedade."""
    criados: dict[str, tuple[int, int]] = {}
    try:
        for suffix in _SIDECARS:
            sidecar = Path(str(caminho) + suffix)
            flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(str(sidecar), flags, 0o600)
            except FileExistsError as exc:
                raise ErroPromocao("Sidecar apareceu durante a reserva exclusiva do destino.") from exc
            except OSError as exc:
                raise ErroPromocao("Não foi possível reservar sidecar de promoção com exclusividade.") from exc
            try:
                info = os.fstat(fd)
                criados[suffix] = (info.st_dev, info.st_ino)
            finally:
                os.close(fd)
    except BaseException:
        for suffix, identity in criados.items():
            sidecar = Path(str(caminho) + suffix)
            try:
                info = sidecar.lstat()
                if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                    sidecar.unlink()
            except OSError:
                pass
        raise
    return criados


def _remover_destino_proprio(
    destino: Path,
    identidade_principal: tuple[int, int],
    sidecars_proprios: Mapping[str, tuple[int, int]],
) -> list[str]:
    preservados: list[str] = []
    try:
        info = destino.lstat()
        if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identidade_principal:
            destino.unlink()
        elif destino.exists() or destino.is_symlink():
            preservados.append(destino.name)
    except OSError:
        preservados.append(destino.name)
    for suffix in _SIDECARS:
        sidecar = Path(str(destino) + suffix)
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        try:
            info = sidecar.lstat()
            expected = sidecars_proprios.get(suffix)
            if (
                expected is not None
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and (info.st_dev, info.st_ino) == expected
            ):
                sidecar.unlink()
            else:
                preservados.append(sidecar.name)
        except OSError:
            preservados.append(sidecar.name)
    return preservados


def promover_staging(
    validado: StagingValidado,
    destino: str | os.PathLike[str],
) -> Path:
    """Cria uma cópia SQLite nova em diretório temporário; não ativa o banco."""
    if not isinstance(validado, StagingValidado):
        raise ErroPromocao("É necessário fornecer um staging previamente validado.")
    config = _configuracao_confiavel()
    if _config_id(config) != validado.config_id:
        raise ErroPromocao("A configuração confiável mudou desde a validação.")
    destino_path = _validar_diretorio_destino(Path(destino).expanduser())
    fontes = dict(validado.fontes)
    novamente_validado = validar_staging(
        validado.staging,
        validado.manifesto,
        validado.assinatura,
        fontes,
    )
    if (
        novamente_validado.manifesto_sha256 != validado.manifesto_sha256
        or novamente_validado.input_identities != validado.input_identities
        or novamente_validado.approval_id != validado.approval_id
    ):
        raise ErroPromocao("Staging, manifesto ou arquivos de confiança mudaram desde a validação.")

    approval_reserved = False
    fd: int | None = None
    destino_info_reservado: os.stat_result | None = None
    sidecars_proprios: dict[str, tuple[int, int]] = {}
    try:
        _reservar_aprovacao(config, validado.approval_id, validado.manifesto_sha256)
        approval_reserved = True
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(destino_path), flags, 0o600)
        except FileExistsError as exc:
            raise ErroPromocao("O destino já existe; sobrescrita não permitida.") from exc
        destino_info_reservado = os.fstat(fd)
        current_path_info = destino_path.lstat()
        if (current_path_info.st_dev, current_path_info.st_ino) != (
            destino_info_reservado.st_dev, destino_info_reservado.st_ino
        ):
            raise ErroPromocao("O destino foi substituído após sua reserva exclusiva.")

        sidecars_proprios.update(_reservar_sidecars_proprios(destino_path))
        origem_conn = _conexao_somente_leitura(validado.staging)
        destino_conn = sqlite3.connect(str(destino_path), timeout=10)
        dest_info_after_open = destino_path.lstat()
        if (dest_info_after_open.st_dev, dest_info_after_open.st_ino) != (
            destino_info_reservado.st_dev, destino_info_reservado.st_ino
        ):
            destino_conn.close()
            origem_conn.close()
            raise ErroPromocao("O destino mudou durante a abertura SQLite.")
        try:
            _executar_backup(origem_conn, destino_conn)
            integrity = destino_conn.execute("PRAGMA integrity_check").fetchone()[0]
            fk_errors = list(destino_conn.execute("PRAGMA foreign_key_check"))
            if integrity != "ok" or fk_errors:
                raise ErroPromocao("A cópia não passou nas verificações SQLite.")
        finally:
            destino_conn.close()
            origem_conn.close()
        current_path_info = destino_path.lstat()
        if (current_path_info.st_dev, current_path_info.st_ino) != (
            destino_info_reservado.st_dev, destino_info_reservado.st_ino
        ):
            raise ErroPromocao("O destino foi substituído durante a cópia SQLite.")
        destino_path.chmod(0o600)
        # A cópia de backup pode não ser byte a byte idêntica; comparamos toda
        # a estrutura e o conteúdo sem alterar staging nem destino.
        persistencia.validar_esquema_banco(destino_path)
        copia_conn = _conexao_somente_leitura(destino_path)
        try:
            copied_fingerprint = _fingerprint_banco(copia_conn)
        finally:
            copia_conn.close()
        origem_conn = _conexao_somente_leitura(validado.staging)
        try:
            source_fingerprint = _fingerprint_banco(origem_conn)
        finally:
            origem_conn.close()
        if copied_fingerprint != source_fingerprint:
            raise ErroPromocao("O conteúdo da cópia diverge do staging aprovado.")
        validado_depois = validar_staging(
            validado.staging,
            validado.manifesto,
            validado.assinatura,
            fontes,
            _permitir_reservada=True,
        )
        if (
            validado_depois.manifesto_sha256 != validado.manifesto_sha256
            or validado_depois.input_identities != validado.input_identities
        ):
            raise ErroPromocao("A aprovação ou staging mudou durante a cópia.")
        destino_validado, destino_info = _validar_arquivo_temporario(
            destino_path, "Banco de promoção"
        )
        if (
            destino_validado != destino_path
            or destino_info is None
            or destino_info_reservado is None
            or (destino_info.st_dev, destino_info.st_ino)
            != (destino_info_reservado.st_dev, destino_info_reservado.st_ino)
            or destino_info.st_mode & 0o077
        ):
            raise ErroPromocao("O arquivo de promoção mudou de identidade ou permissões.")
        _finalizar_aprovacao(config, validado.approval_id, validado.manifesto_sha256)
        approval_reserved = False
        return destino_path
    except BaseException as exc:
        residuals: list[str] = []
        if destino_info_reservado is not None:
            residuals = _remover_destino_proprio(
                destino_path,
                (destino_info_reservado.st_dev, destino_info_reservado.st_ino),
                sidecars_proprios,
            )
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
        if approval_reserved:
            _liberar_reserva_aprovacao(config, validado.approval_id, validado.manifesto_sha256)
        if isinstance(exc, ErroPromocao):
            if residuals:
                raise ErroPromocao(
                    f"Promoção falhou; artefatos sem propriedade comprovada foram preservados: {', '.join(residuals)}."
                ) from exc
            raise
        if residuals:
            raise ErroPromocao(
                f"Promoção falhou; artefatos sem propriedade comprovada foram preservados: {', '.join(residuals)}."
            ) from exc
        raise ErroPromocao("A cópia de promoção falhou e foi removida.") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
