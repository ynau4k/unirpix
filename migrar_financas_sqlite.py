"""Pré-validação e importação controlada dos JSON para SQLite temporário.

O CLI é dry-run por padrão. A opção de importação só aceita cópias dos JSON e
um banco sob tempfile.gettempdir(); não é um mecanismo de corte de produção.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from persistencia_sqlite import (
    ErroPersistencia,
    ErroValidacao,
    LimiteFinanceiroExcedido,
    VALOR_MAXIMO_CENTAVOS,
    _cpf_normalizado,
    converter_valor_legado_para_centavos,
    inicializar_banco,
    transacao_escrita,
    validar_esquema_banco,
)


ARQUIVOS_ORIGEM = ("cadastro.json", "depositos.json", "apostas.json")
ROOT_PROJETO = Path(__file__).resolve().parent


class ErroMigracao(ErroPersistencia):
    """A importação foi recusada para evitar perda ou alteração de dados."""


@dataclass
class RelatorioMigracao:
    dry_run: bool
    elegivel: bool
    contagens_origem: dict[str, int] = field(default_factory=dict)
    problemas: dict[str, int] = field(default_factory=dict)
    avisos: dict[str, int] = field(default_factory=dict)
    estatisticas_monetarias: dict[str, int] = field(default_factory=dict)
    datas_ausentes: dict[str, int] = field(default_factory=dict)
    importado: dict[str, int] = field(default_factory=dict)
    ja_importado: bool = False

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rejeitar_constante_json(valor: str) -> None:
    raise ValueError("constante numérica JSON não padrão")


def _carregar_json(caminho: Path | bytes) -> list[Any]:
    if isinstance(caminho, bytes):
        texto = caminho.decode("utf-8")
    else:
        texto = Path(caminho).read_text(encoding="utf-8")
    dados = json.loads(
        texto,
        parse_float=Decimal,
        parse_constant=_rejeitar_constante_json,
    )
    if not isinstance(dados, list):
        raise ValueError("a raiz JSON deve ser uma lista")
    return dados


def _timestamp_original(registro: dict[str, Any]) -> str | None:
    timestamp = registro.get("data_hora")
    if timestamp is None:
        return None
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("data_hora inválida")
    datetime.fromisoformat(timestamp)
    return timestamp


def _json_decimal(registro: Any, *, sort_keys: bool = False) -> str:
    """Serializa Decimal como literal JSON numérico, sem float ou aspas."""
    if registro is None or isinstance(registro, (bool, str, int)):
        return json.dumps(registro, ensure_ascii=False, allow_nan=False)
    if isinstance(registro, Decimal):
        if not registro.is_finite():
            raise ValueError("Decimal JSON não finito")
        literal = str(registro)
        # Decimal usa sintaxe compatível com números JSON; valide a forma
        # numérica sem converter o valor para float.
        if not re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", literal):
            raise ValueError("Decimal não pode ser representado como número JSON")
        return literal
    if isinstance(registro, float):
        raise TypeError("float não é aceito na serialização dos JSON legados")
    if isinstance(registro, list):
        return "[" + ",".join(_json_decimal(item, sort_keys=sort_keys) for item in registro) + "]"
    if isinstance(registro, dict):
        if not all(isinstance(key, str) for key in registro):
            raise TypeError("Chaves de objetos JSON devem ser texto")
        keys = sorted(registro) if sort_keys else registro.keys()
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" +
            _json_decimal(registro[key], sort_keys=sort_keys)
            for key in keys
        ) + "}"
    raise TypeError(f"Tipo não serializável em JSON: {type(registro).__name__}")


def _canonical_json(registro: Any) -> str:
    return _json_decimal(registro, sort_keys=True)


def _money_category(value: Any, *, signed: bool, allow_zero: bool) -> tuple[str, int | None]:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return "invalid", None
    try:
        decimal = Decimal(value) if isinstance(value, int) else value
    except (InvalidOperation, ValueError):
        return "invalid", None
    if not decimal.is_finite():
        return "invalid", None
    if decimal.as_tuple().exponent < -2:
        return "review", None
    if decimal < 0 and not signed:
        return "invalid", None
    if decimal == 0 and not allow_zero:
        return "invalid", None
    try:
        cents = converter_valor_legado_para_centavos(
            value,
            permitir_negativo=signed,
            permitir_zero=allow_zero,
        )
    except LimiteFinanceiroExcedido:
        return "unsafe", None
    except ErroValidacao:
        return "invalid", None
    return "exact", cents


def _strict_int(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def analisar_jsons(
    fontes: dict[str, str | os.PathLike[str] | bytes], *, dry_run: bool = True
) -> tuple[
    RelatorioMigracao,
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    """Valida fontes sem gravar; retorna relatório agregado e plano em memória."""
    relatorio = RelatorioMigracao(dry_run=dry_run, elegivel=False)
    problemas: Counter[str] = Counter()
    avisos: Counter[str] = Counter()
    money: Counter[str] = Counter()
    datas_ausentes: Counter[str] = Counter()
    registros: dict[str, list[dict[str, Any]]] = {}
    metadados: dict[str, list[dict[str, Any]]] = {}
    cpfs: dict[str, set[str]] = {"cadastro.json": set()}
    erros_registro = False

    for filename in ARQUIVOS_ORIGEM:
        if filename not in fontes:
            problemas["arquivo_fonte_ausente"] += 1
            continue
        try:
            source = fontes[filename]
            rows = _carregar_json(source if isinstance(source, bytes) else Path(source))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            problemas["json_ilegivel_ou_estrutura_invalida"] += 1
            continue
        registros[filename] = rows
        metadados[filename] = [{} for _ in rows]
        relatorio.contagens_origem[filename] = len(rows)
        seen_rows: set[str] = set()
        for position, row in enumerate(rows):
            if not isinstance(row, dict):
                problemas["registro_nao_objeto"] += 1
                erros_registro = True
                continue

            optional = {
                "cadastro.json": {"senha_hash"},
                "depositos.json": {"data_hora"},
                "apostas.json": {"data_hora"},
            }[filename]
            try:
                serialized = _canonical_json(row)
            except (TypeError, ValueError):
                problemas["registro_json_nao_padrao"] += 1
                erros_registro = True
                continue
            if serialized in seen_rows:
                problemas["registro_exato_duplicado"] += 1
                erros_registro = True
            else:
                seen_rows.add(serialized)

            required = {
                "cadastro.json": {"nome", "cpf", "saldo"},
                "depositos.json": {"cpf", "valor"},
                "apostas.json": {
                    "cpf", "valor_apostado", "numero_escolhido",
                    "numero_sorteado", "resultado", "valor_resultado",
                },
            }[filename]
            missing = required - row.keys()
            if missing:
                problemas["campos_obrigatorios_ausentes"] += 1
                erros_registro = True
            if row.keys() - required - optional:
                avisos["campos_adicionais_preservados"] += 1

            cpf_value = row.get("cpf")
            try:
                cpf = _cpf_normalizado(cpf_value)
            except ErroValidacao:
                problemas["cpf_invalido_ou_ausente"] += 1
                erros_registro = True
                cpf = None
            if filename == "cadastro.json" and cpf is not None:
                if cpf in cpfs[filename]:
                    problemas["cpf_duplicado_no_cadastro"] += 1
                    erros_registro = True
                cpfs[filename].add(cpf)
            if cpf is not None:
                metadados[filename][position]["cpf_normalizado"] = cpf

            money_fields: list[tuple[str, bool, bool]]
            if filename == "cadastro.json":
                if not isinstance(row.get("nome"), str) or not row.get("nome", "").strip():
                    problemas["nome_invalido"] += 1
                    erros_registro = True
                if row.get("senha_hash") is not None and (
                    not isinstance(row.get("senha_hash"), str)
                    or not row.get("senha_hash", "").strip()
                ):
                    problemas["hash_senha_invalido"] += 1
                    erros_registro = True
                money_fields = [("saldo", False, True)]
            elif filename == "depositos.json":
                money_fields = [("valor", False, False)]
                if "data_hora" not in row:
                    datas_ausentes[filename] += 1
            else:
                money_fields = [
                    ("valor_apostado", False, False),
                    ("valor_resultado", True, False),
                ]
                if not _strict_int(row.get("numero_escolhido"), 0, 10):
                    problemas["numero_escolhido_invalido"] += 1
                    erros_registro = True
                if not _strict_int(row.get("numero_sorteado"), 0, 10):
                    problemas["numero_sorteado_invalido"] += 1
                    erros_registro = True
                if not isinstance(row.get("resultado"), str) or row.get("resultado") not in {"Vitória!", "Você perdeu!"}:
                    problemas["resultado_de_aposta_invalido"] += 1
                    erros_registro = True
                if "data_hora" not in row:
                    datas_ausentes[filename] += 1

            converted: dict[str, int] = {}
            for field_name, signed, zero_ok in money_fields:
                if field_name not in row:
                    continue
                category, cents = _money_category(
                    row[field_name], signed=signed, allow_zero=zero_ok
                )
                money[category] += 1
                if category != "exact":
                    problem_code = {
                        "review": "valor_com_mais_de_duas_casas",
                        "invalid": "valor_monetario_invalido",
                        "unsafe": "valor_fora_do_limite_seguro",
                    }[category]
                    problemas[problem_code] += 1
                    erros_registro = True
                elif cents is not None:
                    converted[field_name] = cents
            metadados[filename][position]["centavos_legados"] = converted

            if filename == "apostas.json" and len(converted) == 2:
                chosen = row.get("numero_escolhido")
                drawn = row.get("numero_sorteado")
                outcome = row.get("resultado")
                stake = converted.get("valor_apostado")
                delta = converted.get("valor_resultado")
                if (
                    isinstance(chosen, int)
                    and not isinstance(chosen, bool)
                    and isinstance(drawn, int)
                    and not isinstance(drawn, bool)
                    and stake is not None
                    and delta is not None
                ):
                    expected_win = chosen == drawn
                    correct = (
                        (outcome == "Vitória!" and expected_win and delta == stake * 10)
                        or (outcome == "Você perdeu!" and not expected_win and delta == -stake)
                    )
                    if not correct:
                        problemas["regra_historica_inconsistente"] += 1
                        erros_registro = True

            if "data_hora" in row:
                try:
                    _timestamp_original(row)
                except (ValueError, TypeError):
                    problemas["data_hora_invalida"] += 1
                    erros_registro = True

    known_cpfs = cpfs.get("cadastro.json", set())
    if "cadastro.json" in registros:
        for filename in ("depositos.json", "apostas.json"):
            for position, row in enumerate(registros.get(filename, [])):
                if isinstance(row, dict):
                    cpf = metadados[filename][position].get("cpf_normalizado")
                    if cpf is not None and cpf not in known_cpfs:
                        problemas["historico_sem_conta_correspondente"] += 1
                        erros_registro = True

    relatorio.problemas = dict(problemas)
    relatorio.avisos = dict(avisos)
    relatorio.estatisticas_monetarias = dict(money)
    relatorio.datas_ausentes = dict(datas_ausentes)
    todas_fontes = len(registros) == len(ARQUIVOS_ORIGEM)
    relatorio.elegivel = todas_fontes and not erros_registro and not problemas
    return relatorio, registros, metadados


def _fingerprint(registros: dict[str, list[dict[str, Any]]]) -> str:
    data = _canonical_json(
        {filename: registros[filename] for filename in ARQUIVOS_ORIGEM}
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _dentro_do_temp(caminho: Path) -> bool:
    alvo = caminho.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        return os.path.commonpath((str(alvo), str(temp_root))) == str(temp_root)
    except ValueError:
        return False


def _validar_diretorios_temporarios_privados(caminho: Path) -> None:
    raiz_temp = Path(tempfile.gettempdir()).resolve()
    atual = caminho.parent.resolve()
    if atual == raiz_temp:
        raise ErroMigracao("Use um subdiretório privado dentro da raiz temporária.")
    uid_atual = os.getuid() if hasattr(os, "getuid") else None
    while True:
        info = atual.stat()
        if atual == raiz_temp:
            sticky = bool(info.st_mode & stat.S_ISVTX)
            if info.st_mode & 0o022 and not sticky:
                raise ErroMigracao("A raiz temporária permite escrita insegura.")
            return
        if (uid_atual is not None and info.st_uid != uid_atual) or info.st_mode & 0o022:
            raise ErroMigracao("Use um subdiretório temporário privado e controlado pelo usuário.")
        if atual.parent == atual:
            raise ErroMigracao("O caminho não está dentro da raiz temporária configurada.")
        atual = atual.parent


def _ler_copia_temporaria(caminho: Path) -> bytes:
    """Lê o mesmo inode cuja regularidade e ausência de hard links foram verificadas."""
    if caminho.is_symlink():
        raise ErroMigracao("Fontes temporárias não podem ser links simbólicos.")
    resolvido = caminho.expanduser().resolve(strict=True)
    if not _dentro_do_temp(resolvido):
        raise ErroMigracao("Fontes da importação devem estar no diretório temporário.")
    _validar_diretorios_temporarios_privados(resolvido)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descritor = os.open(str(resolvido), flags)
    try:
        info = os.fstat(descritor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ErroMigracao("A fonte deve ser arquivo regular sem hard links.")
        with os.fdopen(descritor, "rb", closefd=False) as arquivo:
            return arquivo.read()
    finally:
        os.close(descritor)


def _validar_destino_temporario(caminho: str | os.PathLike[str]) -> Path:
    entrada = Path(caminho).expanduser()
    if entrada.is_symlink():
        raise ErroMigracao("O banco temporário não pode ser link simbólico.")
    resolvido = entrada.resolve()
    if not _dentro_do_temp(resolvido):
        raise ErroMigracao("O banco de importação deve estar no diretório temporário.")
    _validar_diretorios_temporarios_privados(resolvido)
    if resolvido.exists():
        info = resolvido.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ErroMigracao("O banco temporário deve ser arquivo regular sem hard links.")
    return resolvido


def _garantir_esquema_existente_ou_novo(caminho: Path) -> None:
    if not caminho.exists():
        inicializar_banco(caminho)
        return
    try:
        validar_esquema_banco(caminho)
        conexao = sqlite3.connect(caminho.as_uri() + "?mode=ro", uri=True)
        try:
            objetos = {
                (linha[0], linha[1])
                for linha in conexao.execute(
                    "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            conexao.close()
    except (sqlite3.Error, ErroPersistencia) as exc:
        raise ErroMigracao("O destino existente não é um banco SQLite válido.") from exc
    obrigatorias = {
        ("table", "users"), ("table", "accounts"),
        ("table", "legacy_history"), ("table", "migration_batches"),
        ("view", "deposits"), ("view", "bets"),
    }
    if not obrigatorias.issubset(objetos):
        raise ErroMigracao("O destino existente não tem o esquema de migração esperado.")


def importar_jsons_temporarios(
    fontes: dict[str, str | os.PathLike[str]],
    banco: str | os.PathLike[str],
    *,
    dry_run: bool = True,
) -> RelatorioMigracao:
    """Valida e, opcionalmente, importa cópias dos JSON em banco temporário.

    O modo dry-run é o padrão e não cria banco. A importação só aceita fontes
    e banco dentro do diretório temporário do sistema. Em modo de aplicação,
    cada fonte é aberta uma vez, exige inode único e os bytes validados são os
    mesmos posteriormente importados, reduzindo riscos de hard link e TOCTOU.
    Em banco que contenha somente lotes históricos, novos lotes acrescentam
    usuários/históricos ainda ausentes e preservam saldos e registros anteriores.
    """
    fontes_analisadas: dict[str, str | os.PathLike[str] | bytes] = fontes
    banco_path: Path | None = None
    fontes_paths: list[Path] = []
    if not dry_run:
        if not all(nome in fontes for nome in ARQUIVOS_ORIGEM):
            raise ErroMigracao("As três fontes JSON temporárias são obrigatórias.")
        banco_path = _validar_destino_temporario(banco)
        fontes_bytes: dict[str, bytes] = {}
        for nome in ARQUIVOS_ORIGEM:
            caminho_fonte = Path(fontes[nome]).expanduser()
            fonte_resolvida = caminho_fonte.resolve(strict=True)
            if not _dentro_do_temp(fonte_resolvida):
                raise ErroMigracao("Fontes da importação devem estar no diretório temporário.")
            fontes_paths.append(fonte_resolvida)
            fontes_bytes[nome] = _ler_copia_temporaria(caminho_fonte)
        if any(path == banco_path for path in fontes_paths):
            raise ErroMigracao("O banco não pode substituir um JSON de origem.")
        fontes_analisadas = fontes_bytes

    relatorio, registros, metadados = analisar_jsons(fontes_analisadas, dry_run=dry_run)
    if dry_run or not relatorio.elegivel:
        return relatorio

    if banco_path is None:
        raise ErroMigracao("Fontes e banco temporários são obrigatórios para importar.")

    fingerprint = _fingerprint(registros)
    _garantir_esquema_existente_ou_novo(banco_path)
    imported_counts = {"users": 0, "financial_transactions": 0, "legacy_history": 0}
    existing = sqlite3.connect(banco_path.as_uri() + "?mode=ro", uri=True)
    try:
        existing.row_factory = sqlite3.Row
        found = existing.execute(
            "SELECT 1 FROM migration_batches WHERE bundle_hash = ?", (fingerprint,)
        ).fetchone()
        if found:
            relatorio.importado = imported_counts
            relatorio.ja_importado = True
            relatorio.dry_run = False
            return relatorio
        stats = existing.execute(
            """SELECT
                 (SELECT COUNT(*) FROM users) AS users,
                 (SELECT COUNT(*) FROM financial_transactions) AS transactions,
                 (SELECT COUNT(*) FROM financial_transactions WHERE operation_type <> 'opening_balance') AS live_transactions,
                 (SELECT COUNT(*) FROM legacy_history) AS histories,
                 (SELECT COUNT(*) FROM idempotency_keys) AS idempotency_keys,
                 (SELECT COUNT(*) FROM migration_batches) AS batches,
                 (SELECT COUNT(*) FROM users AS u WHERE NOT EXISTS
                    (SELECT 1 FROM accounts AS a WHERE a.user_id = u.user_id)) AS accounts_missing,
                 (SELECT COUNT(*) FROM accounts AS a WHERE NOT EXISTS
                    (SELECT 1 FROM financial_transactions AS ft
                     WHERE ft.user_id = a.user_id AND ft.operation_type = 'opening_balance')) AS openings_missing"""
        ).fetchone()
        existing_users = {
            str(row["cpf"]): int(row["user_id"])
            for row in existing.execute("SELECT user_id, cpf FROM users")
        }
    finally:
        existing.close()
    has_prior_data = any(
        stats[name] for name in ("users", "transactions", "histories", "idempotency_keys")
    )
    if stats["batches"] == 0 and has_prior_data:
        raise ErroMigracao("O banco temporário já contém dados de outra importação.")
    if stats["batches"] and (
        stats["live_transactions"] or stats["idempotency_keys"]
        or stats["accounts_missing"] or stats["openings_missing"]
    ):
        raise ErroMigracao("Importação incremental só é aceita em banco legado consistente e sem operações ativas.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    user_ids: dict[str, int] = dict(existing_users)
    try:
        with transacao_escrita(banco_path) as conexao:
            conexao.execute(
                """INSERT INTO migration_batches
                   (bundle_hash, imported_at, account_count, deposit_history_count, bet_history_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    fingerprint,
                    now,
                    len(registros["cadastro.json"]),
                    len(registros["depositos.json"]),
                    len(registros["apostas.json"]),
                ),
            )
            for row_index, row in enumerate(registros["cadastro.json"]):
                meta = metadados["cadastro.json"][row_index]
                cpf = meta["cpf_normalizado"]
                if cpf in user_ids:
                    continue
                balance = meta["centavos_legados"]["saldo"]
                cursor = conexao.execute(
                    "INSERT INTO users(cpf, nome, senha_hash) VALUES (?, ?, ?)",
                    (cpf, row["nome"], row.get("senha_hash")),
                )
                user_id = int(cursor.lastrowid)
                user_ids[cpf] = user_id
                conexao.execute(
                    "INSERT INTO accounts(user_id, balance_cents) VALUES (?, 0)",
                    (user_id,),
                )
                conexao.execute(
                    """INSERT INTO financial_transactions
                       (user_id, operation_type, delta_cents, occurred_at, source_file, source_row)
                       VALUES (?, 'opening_balance', ?, ?, 'cadastro.json', ?)""",
                    (user_id, balance, now, row_index),
                )
                imported_counts["users"] += 1
                imported_counts["financial_transactions"] += 1

            for filename, history_type in (("depositos.json", "deposit"), ("apostas.json", "bet")):
                for row_index, row in enumerate(registros[filename]):
                    canonical_original = {
                        key: value for key, value in row.items()
                    }
                    original_data = _canonical_json(canonical_original)
                    record_hash = hashlib.sha256(
                        f"{filename}\0{original_data}".encode("utf-8")
                    ).hexdigest()
                    exists = conexao.execute(
                        "SELECT 1 FROM legacy_history WHERE source_file = ? AND record_hash = ?",
                        (filename, record_hash),
                    ).fetchone()
                    if exists:
                        continue
                    cursor = conexao.execute(
                        """INSERT INTO legacy_history
                           (bundle_hash, source_file, source_row, history_type, user_id,
                            original_data_json, original_datetime, record_hash)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fingerprint,
                            filename,
                            row_index,
                            history_type,
                            user_ids[metadados[filename][row_index]["cpf_normalizado"]],
                            original_data,
                            _timestamp_original(row),
                            record_hash,
                        ),
                    )
                    del cursor
                    imported_counts["legacy_history"] += 1

    except sqlite3.Error as exc:
        raise ErroMigracao("A importação falhou e foi revertida integralmente.") from exc

    relatorio.dry_run = False
    relatorio.importado = imported_counts
    return relatorio


def _argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cadastro", default=str(ROOT_PROJETO / "cadastro.json"))
    parser.add_argument("--depositos", default=str(ROOT_PROJETO / "depositos.json"))
    parser.add_argument("--apostas", default=str(ROOT_PROJETO / "apostas.json"))
    parser.add_argument("--banco", help="Caminho para banco temporário; nunca aceita produção.")
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Importa somente quando fontes e banco estão em diretório temporário.",
    )
    return parser.parse_args()


def main() -> int:
    args = _argumentos()
    fontes = {
        "cadastro.json": args.cadastro,
        "depositos.json": args.depositos,
        "apostas.json": args.apostas,
    }
    if args.aplicar and not args.banco:
        print(json.dumps({"error": "banco_temporario_obrigatorio"}, ensure_ascii=True))
        return 2
    try:
        if args.aplicar:
            report = importar_jsons_temporarios(fontes, args.banco, dry_run=False)
        else:
            report, _, _ = analisar_jsons(fontes, dry_run=True)
        print(json.dumps(report.como_dict(), ensure_ascii=True, sort_keys=True))
        return 0 if report.elegivel else 1
    except (ErroMigracao, OSError, sqlite3.Error):
        print(json.dumps({"error": "migracao_recusada_ou_revertida"}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
