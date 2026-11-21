"""Persistência financeira isolada em SQLite, usando centavos inteiros.

Este módulo não integra com Flask e não gera números aleatórios. A função de
apostas recebe um sorteador injetado para permitir testes determinísticos.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Callable, Iterator


VALOR_MAXIMO_CENTAVOS = 100_000_000_000  # R$ 1.000.000.000,00
ARQUIVO_ESQUEMA = Path(__file__).with_name("esquema_financeiro.sql")
VERSAO_ESQUEMA = 7


class ErroPersistencia(Exception):
    """Erro base da camada de persistência financeira."""


class ErroValidacao(ErroPersistencia, ValueError):
    """Entrada ou regra financeira inválida."""


class ContaNaoEncontrada(ErroPersistencia):
    """A conta indicada não existe."""


class SaldoInsuficiente(ErroPersistencia):
    """A conta não cobre uma possível derrota."""


class LimiteFinanceiroExcedido(ErroPersistencia):
    """Uma operação ou saldo excederia o limite definido."""


class ConflitoIdempotencia(ErroPersistencia):
    """Uma chave idempotente existente tem estado inconsistente."""


class UsuarioJaExiste(ErroPersistencia):
    """Já existe um usuário com o CPF informado."""


class VersaoCredencialDesatualizada(ErroPersistencia):
    """A credencial foi alterada por outra operação concorrente."""


class ContaInativa(ErroPersistencia):
    """A conta está inativa e não pode usar a operação solicitada."""


class AutorizacaoAdministrativaIndisponivel(ErroPersistencia):
    """Não há mecanismo administrativo confiável configurado nesta etapa."""


@dataclass(frozen=True)
class ResultadoOperacao:
    transaction_id: int
    user_id: int
    operation_type: str
    delta_cents: int
    balance_cents: int
    amount_cents: int
    outcome: str | None = None
    chosen_number: int | None = None
    drawn_number: int | None = None
    replayed: bool = False
    slot_symbols: tuple[int, int, int] | None = None


def _cpf_normalizado(cpf: str) -> str:
    if not isinstance(cpf, str):
        raise ErroValidacao("CPF inválido.")
    numeros = re.sub(r"\D", "", cpf)
    if len(numeros) != 11 or len(set(numeros)) == 1:
        raise ErroValidacao("CPF inválido.")
    digitos = [int(numero) for numero in numeros]
    for posicao in (9, 10):
        soma = sum(digitos[indice] * (posicao + 1 - indice) for indice in range(posicao))
        resto = (soma * 10) % 11
        calculado = 0 if resto == 10 else resto
        if digitos[posicao] != calculado:
            raise ErroValidacao("CPF inválido.")
    return numeros


def _hash_senha_validado(senha_hash: object) -> str:
    """Valida hash Werkzeug e limites de custo antes do armazenamento.

    Política aceita: scrypt N=2**14..2**18, r=8, p=1..2,
    N*r*p <= 1_048_576; ou PBKDF2 com 600_000..2_000_000 iterações.
    Salt deve ter 8..64 caracteres alfanuméricos. Hashes legados abaixo
    desses mínimos são recusados e precisam de recuperação controlada.
    """
    if not isinstance(senha_hash, str) or not senha_hash or senha_hash != senha_hash.strip():
        raise ErroValidacao("Hash de senha inválido.")
    partes = senha_hash.split("$")
    if len(partes) != 3:
        raise ErroValidacao("Hash de senha inválido.")
    metodo, salt, digest = partes
    if not re.fullmatch(r"[A-Za-z0-9]{8,64}", salt):
        raise ErroValidacao("Hash de senha inválido.")
    if not re.fullmatch(r"[0-9a-f]{40,128}", digest) or len(digest) % 2:
        raise ErroValidacao("Hash de senha inválido.")

    scrypt = re.fullmatch(r"scrypt:(\d+):(\d+):(\d+)", metodo)
    if scrypt:
        n, r, p = (int(parte) for parte in scrypt.groups())
        if (
            n < 16_384 or n > 262_144 or n & (n - 1)
            or r != 8 or p < 1 or p > 2 or n * r * p > 1_048_576
        ):
            raise ErroValidacao("Hash de senha inválido.")
        if len(digest) != 128:
            raise ErroValidacao("Hash de senha inválido.")
        return senha_hash

    pbkdf2 = re.fullmatch(r"pbkdf2:(sha1|sha224|sha256|sha384|sha512):(\d+)", metodo)
    if pbkdf2:
        iterations = int(pbkdf2.group(2))
        digest_lengths = {"sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}
        if not 600_000 <= iterations <= 2_000_000 or len(digest) != digest_lengths[pbkdf2.group(1)]:
            raise ErroValidacao("Hash de senha inválido.")
        return senha_hash
    raise ErroValidacao("Hash de senha inválido.")


def _decimal_de_entrada(
    valor: object, *, limite_centavos: int = VALOR_MAXIMO_CENTAVOS
) -> Decimal:
    """Converte uma entrada sem aceitar float como etapa intermediária."""
    if isinstance(valor, bool) or isinstance(valor, float):
        raise ErroValidacao("Informe um valor decimal válido.")
    try:
        if isinstance(valor, Decimal):
            decimal = valor
        elif isinstance(valor, int):
            decimal = Decimal(valor)
        elif isinstance(valor, str):
            texto = valor.strip()
            if not texto:
                raise ErroValidacao("Informe um valor decimal válido.")
            if "," in texto and "." in texto:
                raise ErroValidacao("Use somente um separador decimal.")
            if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", texto):
                raise ErroValidacao("Informe um valor decimal válido.")
            decimal = Decimal(texto.replace(",", "."))
        else:
            raise ErroValidacao("Informe um valor decimal válido.")
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, ErroValidacao):
            raise
        raise ErroValidacao("Informe um valor decimal válido.") from None
    if not decimal.is_finite():
        raise ErroValidacao("O valor deve ser finito.")
    if decimal.as_tuple().exponent < -2:
        raise ErroValidacao("O valor não pode ter mais de duas casas decimais.")
    if abs(decimal) > Decimal(limite_centavos) / Decimal(100):
        raise LimiteFinanceiroExcedido("O valor excede o limite por operação.")
    return decimal


def converter_para_centavos(
    valor: object,
    *,
    exigir_positivo: bool = True,
    limite_centavos: int = VALOR_MAXIMO_CENTAVOS,
) -> int:
    """Converte uma entrada monetária exata para centavos inteiros."""
    decimal = _decimal_de_entrada(valor, limite_centavos=limite_centavos)
    if exigir_positivo and decimal <= 0:
        raise ErroValidacao("O valor deve ser positivo.")
    if not exigir_positivo and decimal < 0:
        raise ErroValidacao("O valor não pode ser negativo.")

    with localcontext() as contexto:
        contexto.prec = max(28, len(decimal.as_tuple().digits) + 4)
        centavos_decimal = decimal * Decimal(100)
        inteiro = centavos_decimal.to_integral_value()
        if centavos_decimal != inteiro:
            raise ErroValidacao("O valor não é representável exatamente em centavos.")
        centavos = int(inteiro)

    if abs(centavos) > limite_centavos:
        raise LimiteFinanceiroExcedido("O valor excede o limite por operação.")
    return centavos


def converter_valor_legado_para_centavos(
    valor: object,
    *,
    permitir_negativo: bool = False,
    permitir_zero: bool = True,
) -> int:
    """Converte um número JSON legado via Decimal(str(valor)), sem arredondar."""
    if isinstance(valor, bool) or not isinstance(valor, (int, Decimal)):
        raise ErroValidacao("O valor legado não é numérico.")
    try:
        decimal = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ErroValidacao("O valor legado é inválido.") from None
    if not decimal.is_finite():
        raise ErroValidacao("O valor legado não é finito.")
    if decimal.as_tuple().exponent < -2:
        raise ErroValidacao("O valor legado tem mais de duas casas decimais.")
    if abs(decimal) > Decimal(VALOR_MAXIMO_CENTAVOS) / Decimal(100):
        raise LimiteFinanceiroExcedido("O valor legado excede o limite financeiro.")
    if decimal < 0 and not permitir_negativo:
        raise ErroValidacao("O valor legado não pode ser negativo.")
    if decimal == 0 and not permitir_zero:
        raise ErroValidacao("O valor legado deve ser positivo.")
    with localcontext() as contexto:
        contexto.prec = max(28, len(decimal.as_tuple().digits) + 4)
        centavos_decimal = decimal * Decimal(100)
        if centavos_decimal != centavos_decimal.to_integral_value():
            raise ErroValidacao("O valor legado não é representável em centavos.")
        centavos = int(centavos_decimal)
    if abs(centavos) > VALOR_MAXIMO_CENTAVOS:
        raise LimiteFinanceiroExcedido("O valor legado excede o limite financeiro.")
    return centavos


def _centavos_inteiros(valor: object, *, permitir_zero: bool = True) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErroValidacao("O valor deve ser informado em centavos inteiros.")
    if valor < 0 or (valor == 0 and not permitir_zero):
        raise ErroValidacao("O valor em centavos é inválido.")
    if valor > VALOR_MAXIMO_CENTAVOS:
        raise LimiteFinanceiroExcedido("O valor excede o limite financeiro.")
    return valor


def _numero_jogo(numero: object, nome: str) -> int:
    if isinstance(numero, bool):
        raise ErroValidacao(f"{nome} deve ser um inteiro de 0 a 10.")
    if isinstance(numero, str) and re.fullmatch(r"\s*\d+\s*", numero):
        numero = int(numero.strip())
    if not isinstance(numero, int) or not 0 <= numero <= 10:
        raise ErroValidacao(f"{nome} deve ser um inteiro de 0 a 10.")
    return numero


def _agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _caminho(caminho: str | os.PathLike[str]) -> Path:
    return Path(caminho).expanduser().resolve()


def _executar_declaracoes_sql(conexao: sqlite3.Connection, script: str) -> None:
    """Executa declarações completas sem abandonar a transação do chamador."""
    buffer = ""
    for linha in script.splitlines(keepends=True):
        buffer += linha
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                _executar_declaracao_sql(conexao, buffer)
            buffer = ""
    if buffer.strip():
        raise ErroPersistencia("O esquema SQL termina com uma declaração incompleta.")


def _executar_declaracao_sql(conexao: sqlite3.Connection, declaracao: str) -> None:
    conexao.execute(declaracao)


def _objetos_esquema(conexao: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (str(row["type"]), str(row["name"])): " ".join(str(row["sql"]).split())
        for row in conexao.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        )
    }


def _validar_esquema_existente(conexao: sqlite3.Connection, script: str) -> None:
    objetos = _objetos_esquema(conexao)
    if not objetos:
        return
    if ("table", "schema_metadata") not in objetos:
        raise ErroPersistencia("O banco existente não possui versão de esquema reconhecida.")

    esperado = sqlite3.connect(":memory:")
    try:
        esperado.row_factory = sqlite3.Row
        _executar_declaracoes_sql(esperado, script)
        if objetos != _objetos_esquema(esperado):
            raise ErroPersistencia(
                "O esquema SQLite existente é incompatível; nenhum dado foi alterado."
            )
    finally:
        esperado.close()

    metadata = conexao.execute(
        "SELECT schema_version, schema_sha256 FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    if (
        metadata is None
        or metadata["schema_version"] != VERSAO_ESQUEMA
        or metadata["schema_sha256"] != digest
    ):
        raise ErroPersistencia("A versão ou assinatura do esquema SQLite não é compatível.")
    integrity = [linha[0] for linha in conexao.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise ErroPersistencia("A verificação de integridade SQLite falhou.")
    foreign_key_errors = list(conexao.execute("PRAGMA foreign_key_check"))
    if foreign_key_errors:
        raise ErroPersistencia("O banco contém referências estrangeiras inválidas.")


def inicializar_banco(caminho: str | os.PathLike[str]) -> Path:
    """Cria atomicamente um esquema novo ou valida um esquema existente."""
    banco = _caminho(caminho)
    banco.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    script = ARQUIVO_ESQUEMA.read_text(encoding="utf-8")
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    conexao = sqlite3.connect(str(banco), timeout=10, isolation_level=None)
    try:
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        conexao.execute("PRAGMA busy_timeout = 10000")
        _validar_esquema_existente(conexao, script)
        if not _objetos_esquema(conexao):
            conexao.execute("PRAGMA journal_mode = WAL")
            conexao.execute("PRAGMA synchronous = FULL")
            conexao.execute("BEGIN IMMEDIATE")
            try:
                _executar_declaracoes_sql(conexao, script)
                conexao.execute(
                    "INSERT INTO schema_metadata(singleton, schema_version, schema_sha256) "
                    "VALUES (1, ?, ?)",
                    (VERSAO_ESQUEMA, digest),
                )
                _validar_esquema_existente(conexao, script)
                conexao.commit()
            except BaseException:
                if conexao.in_transaction:
                    conexao.rollback()
                raise
        else:
            conexao.execute("PRAGMA journal_mode = WAL")
            conexao.execute("PRAGMA synchronous = FULL")
    finally:
        conexao.close()
    try:
        banco.chmod(0o600)
    except OSError:
        pass
    return banco


def validar_esquema_banco(caminho: str | os.PathLike[str]) -> None:
    """Valida em modo somente leitura o esquema e sua versão registrada."""
    banco = _caminho(caminho)
    if not banco.is_file():
        raise FileNotFoundError("O banco SQLite não foi inicializado.")
    script = ARQUIVO_ESQUEMA.read_text(encoding="utf-8")
    conexao = sqlite3.connect(banco.as_uri() + "?mode=ro", uri=True)
    try:
        conexao.row_factory = sqlite3.Row
        _validar_esquema_existente(conexao, script)
        if not _objetos_esquema(conexao):
            raise ErroPersistencia("O banco SQLite não possui esquema inicializado.")
    finally:
        conexao.close()


def abrir_conexao(caminho: str | os.PathLike[str]) -> sqlite3.Connection:
    """Abre um banco existente; não cria arquivos caso o caminho esteja ausente."""
    banco = _caminho(caminho)
    if not banco.is_file():
        raise FileNotFoundError("O banco SQLite não foi inicializado.")
    conexao = sqlite3.connect(str(banco), timeout=10, isolation_level=None)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 10000")
    conexao.execute("PRAGMA synchronous = FULL")
    return conexao


@contextmanager
def transacao_escrita(caminho: str | os.PathLike[str]) -> Iterator[sqlite3.Connection]:
    """Usa BEGIN IMMEDIATE e reverte integralmente em qualquer falha."""
    conexao = abrir_conexao(caminho)
    try:
        conexao.execute("BEGIN IMMEDIATE")
        yield conexao
        conexao.commit()
    except BaseException:
        if conexao.in_transaction:
            conexao.rollback()
        raise
    finally:
        conexao.close()


def _registrar_transacao(
    conexao: sqlite3.Connection,
    user_id: int,
    operation_type: str,
    delta_cents: int,
    occurred_at: str,
    *,
    source_file: str | None = None,
    source_row: int | None = None,
    amount_cents: int | None = None,
    wager_cents: int | None = None,
    chosen_number: int | None = None,
    drawn_number: int | None = None,
    outcome: str | None = None,
    slot_symbols: tuple[int, int, int] | None = None,
) -> int:
    cursor = conexao.execute(
        """INSERT INTO financial_transactions
           (user_id, operation_type, delta_cents, occurred_at, source_file, source_row,
            amount_cents, wager_cents, chosen_number, drawn_number, outcome,
            slot_symbol_1, slot_symbol_2, slot_symbol_3)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, operation_type, delta_cents, occurred_at, source_file, source_row,
            amount_cents, wager_cents, chosen_number, drawn_number, outcome,
            *(slot_symbols if slot_symbols is not None else (None, None, None)),
        ),
    )
    return int(cursor.lastrowid)


def criar_usuario(
    caminho: str | os.PathLike[str],
    cpf: str,
    nome: str,
    senha_hash: str,
) -> int:
    """Cria usuário, conta zerada e transação inicial usando um hash pré-gerado."""
    cpf_limpo = _cpf_normalizado(cpf)
    if not isinstance(nome, str) or not nome.strip():
        raise ErroValidacao("Nome inválido.")
    hash_validado = _hash_senha_validado(senha_hash)
    instante = _agora_utc()
    with transacao_escrita(caminho) as conexao:
        if conexao.execute("SELECT 1 FROM users WHERE cpf = ?", (cpf_limpo,)).fetchone():
            raise UsuarioJaExiste("Já existe uma conta para os dados informados.")
        cursor = conexao.execute(
            "INSERT INTO users(cpf, nome, senha_hash) VALUES (?, ?, ?)",
            (cpf_limpo, nome.strip(), hash_validado),
        )
        user_id = int(cursor.lastrowid)
        conexao.execute("INSERT INTO accounts(user_id, balance_cents) VALUES (?, 0)", (user_id,))
        _registrar_transacao(conexao, user_id, "opening_balance", 0, instante)
    return user_id


def consultar_usuario_por_cpf(
    caminho: str | os.PathLike[str], cpf: str
) -> dict[str, object] | None:
    """Retorna a projeção mínima de autenticação para um CPF válido."""
    cpf_limpo = _cpf_normalizado(cpf)
    conexao = abrir_conexao(caminho)
    try:
        linha = conexao.execute(
            "SELECT user_id, cpf, nome, senha_hash, ativo, credential_version "
            "FROM users WHERE cpf = ?",
            (cpf_limpo,),
        ).fetchone()
        return dict(linha) if linha is not None else None
    finally:
        conexao.close()


def consultar_usuario_por_id(
    caminho: str | os.PathLike[str], user_id: int
) -> dict[str, object] | None:
    """Retorna a projeção mínima de autenticação para um identificador interno."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ErroValidacao("Identificador interno inválido.")
    conexao = abrir_conexao(caminho)
    try:
        linha = conexao.execute(
            "SELECT user_id, cpf, nome, senha_hash, ativo, credential_version "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(linha) if linha is not None else None
    finally:
        conexao.close()


def consultar_estado_credencial(
    caminho: str | os.PathLike[str], user_id: int
) -> dict[str, int | bool] | None:
    """Consulta apenas o estado ativo e a versão, sem retornar hash ou CPF."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ErroValidacao("Identificador interno inválido.")
    conexao = abrir_conexao(caminho)
    try:
        linha = conexao.execute(
            "SELECT user_id, ativo, credential_version FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if linha is None:
            return None
        return {
            "user_id": int(linha["user_id"]),
            "ativo": bool(linha["ativo"]),
            "credential_version": int(linha["credential_version"]),
        }
    finally:
        conexao.close()


def _validar_identificador_usuario(user_id: object, nome: str) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ErroValidacao(f"{nome} inválido.")
    return user_id


def _validar_versao_credencial(versao: object) -> int:
    if isinstance(versao, bool) or not isinstance(versao, int) or versao < 1:
        raise ErroValidacao("Versão de credenciais inválida.")
    return versao


def _atualizar_hash_credencial(
    conexao: sqlite3.Connection,
    user_id: int,
    novo_hash: str,
    versao_esperada: int,
    *,
    permitir_inativa: bool,
) -> tuple[int, int]:
    usuario = conexao.execute(
        "SELECT ativo, senha_hash, credential_version FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if usuario is None:
        raise ContaNaoEncontrada("Conta não encontrada.")
    if not usuario["ativo"] and not permitir_inativa:
        raise ContaInativa("A conta está inativa.")
    versao_atual = int(usuario["credential_version"])
    if versao_atual != versao_esperada:
        raise VersaoCredencialDesatualizada("A credencial foi atualizada por outra operação.")
    if usuario["senha_hash"] == novo_hash:
        raise ErroValidacao("A nova credencial deve ser diferente da atual.")
    cursor = conexao.execute(
        "UPDATE users SET senha_hash = ?, credential_version = credential_version + 1 "
        "WHERE user_id = ? AND credential_version = ?",
        (novo_hash, user_id, versao_esperada),
    )
    if cursor.rowcount != 1:
        raise VersaoCredencialDesatualizada("A credencial foi atualizada por outra operação.")
    return versao_atual, versao_atual + 1


def atualizar_hash_senha(
    caminho: str | os.PathLike[str],
    user_id: int,
    novo_hash: str,
    versao_esperada: int,
) -> int:
    """Atualiza hash internamente; o trigger grava auditoria na mesma transação.

    Não exponha esta função diretamente como endpoint. A camada Flask futura
    deverá validar a sessão e vincular user_id exclusivamente à identidade dela.
    """
    user_id = _validar_identificador_usuario(user_id, "Identificador interno")
    novo_hash = _hash_senha_validado(novo_hash)
    versao_esperada = _validar_versao_credencial(versao_esperada)
    try:
        with transacao_escrita(caminho) as conexao:
            _anterior, nova = _atualizar_hash_credencial(
                conexao, user_id, novo_hash, versao_esperada, permitir_inativa=False
            )
    except sqlite3.Error:
        raise ErroPersistencia("Não foi possível atualizar a credencial.") from None
    return nova


def redefinir_hash_senha_administrativamente(
    caminho: str | os.PathLike[str],
    administrator_user_id: int,
    account_user_id: int,
    novo_hash: str,
    versao_esperada: int,
    protocolo: str,
) -> int:
    """Recusa resets até existir autorização administrativa verificável.

    Um ID de usuário ativo e um protocolo não provam privilégio. Nenhum dado
    é lido ou alterado. Esta operação permanece indisponível nesta etapa.
    """
    del caminho, administrator_user_id, account_user_id, novo_hash
    del versao_esperada, protocolo
    raise AutorizacaoAdministrativaIndisponivel(
        "Redefinição administrativa indisponível nesta etapa."
    )


def consultar_conta(
    caminho: str | os.PathLike[str], user_id: int
) -> dict[str, object] | None:
    conexao = abrir_conexao(caminho)
    try:
        linha = conexao.execute(
            """SELECT u.user_id, u.cpf, u.nome, u.senha_hash, u.ativo,
                      u.credential_version, a.balance_cents
               FROM users AS u JOIN accounts AS a USING(user_id)
               WHERE u.user_id = ?""",
            (user_id,),
        ).fetchone()
        return dict(linha) if linha is not None else None
    finally:
        conexao.close()


def consultar_conta_por_cpf(
    caminho: str | os.PathLike[str], cpf: str
) -> dict[str, object] | None:
    cpf_limpo = _cpf_normalizado(cpf)
    conexao = abrir_conexao(caminho)
    try:
        linha = conexao.execute(
            """SELECT u.user_id, u.cpf, u.nome, u.senha_hash, u.ativo,
                      u.credential_version, a.balance_cents
               FROM users AS u JOIN accounts AS a USING(user_id)
               WHERE u.cpf = ?""",
            (cpf_limpo,),
        ).fetchone()
        return dict(linha) if linha is not None else None
    finally:
        conexao.close()


def _hash_chave_idempotencia(user_id: int, operation_type: str, key: str) -> str:
    if not isinstance(key, str) or not 16 <= len(key) <= 512:
        raise ErroValidacao("Chave de idempotência inválida.")
    return hashlib.sha256(f"{user_id}:{operation_type}:{key}".encode("utf-8")).hexdigest()


def gerar_chave_idempotencia() -> str:
    """Gera token opaco com 256 bits de entropia para o chamador guardar no formulário."""
    return secrets.token_urlsafe(32)


def _resultado_existente(
    conexao: sqlite3.Connection, key_hash: str
) -> ResultadoOperacao | None:
    linha = conexao.execute(
        "SELECT state, result_json FROM idempotency_keys WHERE key_hash = ?",
        (key_hash,),
    ).fetchone()
    if linha is None:
        return None
    if linha["state"] != "completed" or not linha["result_json"]:
        raise ConflitoIdempotencia("A chave de idempotência tem estado incompleto.")
    dados = json.loads(linha["result_json"])
    dados["replayed"] = True
    if isinstance(dados.get("slot_symbols"), list):
        dados["slot_symbols"] = tuple(dados["slot_symbols"])
    return ResultadoOperacao(**dados)


def validar_chave_idempotencia(
    caminho: str | os.PathLike[str],
    user_id: int,
    operation_type: str,
    key: str,
) -> ResultadoOperacao | None:
    if operation_type not in {"deposit", "bet", "slot"}:
        raise ErroValidacao("Tipo de operação inválido.")
    key_hash = _hash_chave_idempotencia(user_id, operation_type, key)
    conexao = abrir_conexao(caminho)
    try:
        return _resultado_existente(conexao, key_hash)
    finally:
        conexao.close()


def _iniciar_idempotencia(
    conexao: sqlite3.Connection,
    key_hash: str,
    user_id: int,
    operation_type: str,
    instante: str,
) -> None:
    conexao.execute(
        """INSERT INTO idempotency_keys
           (key_hash, user_id, operation_type, state, created_at)
           VALUES (?, ?, ?, 'processing', ?)""",
        (key_hash, user_id, operation_type, instante),
    )


def _finalizar_idempotencia(
    conexao: sqlite3.Connection,
    key_hash: str,
    resultado: ResultadoOperacao,
) -> None:
    cursor = conexao.execute(
        """UPDATE idempotency_keys
           SET state = 'completed', transaction_id = ?, result_json = ?
           WHERE key_hash = ? AND state = 'processing'""",
        (
            resultado.transaction_id,
            json.dumps(asdict(resultado), separators=(",", ":"), ensure_ascii=True),
            key_hash,
        ),
    )
    if cursor.rowcount != 1:
        raise ErroPersistencia("Não foi possível finalizar exatamente uma chave de idempotência.")


def registrar_deposito(
    caminho: str | os.PathLike[str],
    user_id: int,
    valor: object,
    chave_idempotencia: str,
    *,
    ocorrido_em: str | None = None,
) -> ResultadoOperacao:
    valor_centavos = converter_para_centavos(valor)
    key_hash = _hash_chave_idempotencia(user_id, "deposit", chave_idempotencia)
    instante = ocorrido_em or _agora_utc()

    with transacao_escrita(caminho) as conexao:
        repetida = _resultado_existente(conexao, key_hash)
        if repetida is not None:
            return repetida
        conta = conexao.execute(
            "SELECT balance_cents FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        if conta is None:
            raise ContaNaoEncontrada("Conta não encontrada.")
        saldo_atual = int(conta["balance_cents"])
        novo_saldo = saldo_atual + valor_centavos
        if novo_saldo > VALOR_MAXIMO_CENTAVOS:
            raise LimiteFinanceiroExcedido("O depósito excede o limite máximo de saldo.")

        _iniciar_idempotencia(conexao, key_hash, user_id, "deposit", instante)
        transaction_id = _registrar_transacao(
            conexao, user_id, "deposit", valor_centavos, instante,
            amount_cents=valor_centavos,
        )
        resultado = ResultadoOperacao(
            transaction_id=transaction_id,
            user_id=user_id,
            operation_type="deposit",
            delta_cents=valor_centavos,
            balance_cents=novo_saldo,
            amount_cents=valor_centavos,
        )
        _finalizar_idempotencia(conexao, key_hash, resultado)
    return resultado


def registrar_aposta(
    caminho: str | os.PathLike[str],
    user_id: int,
    valor: object,
    numero_escolhido: object,
    sortear: Callable[[], int],
    chave_idempotencia: str,
    *,
    ocorrido_em: str | None = None,
) -> ResultadoOperacao:
    """Registra aposta transacionalmente; sorteio ocorre após validações.

    O chamador injeta o sorteador. Este módulo nunca executa um sorteio real.
    """
    aposta_centavos = converter_para_centavos(valor)
    escolhido = _numero_jogo(numero_escolhido, "Número escolhido")
    if not callable(sortear):
        raise ErroValidacao("É necessário fornecer o sorteador.")
    key_hash = _hash_chave_idempotencia(user_id, "bet", chave_idempotencia)
    instante = ocorrido_em or _agora_utc()

    with transacao_escrita(caminho) as conexao:
        repetida = _resultado_existente(conexao, key_hash)
        if repetida is not None:
            return repetida
        conta = conexao.execute(
            "SELECT balance_cents FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        if conta is None:
            raise ContaNaoEncontrada("Conta não encontrada.")
        saldo_atual = int(conta["balance_cents"])
        if aposta_centavos > saldo_atual:
            raise SaldoInsuficiente("Saldo insuficiente para cobrir uma eventual derrota.")
        # A vitória credita 10x sem débito prévio; verificamos o limite máximo
        # antes de chamar o sorteador para evitar um sorteio sem capacidade de crédito.
        if saldo_atual + aposta_centavos * 10 > VALOR_MAXIMO_CENTAVOS:
            raise LimiteFinanceiroExcedido("O saldo não comporta o prêmio máximo permitido.")

        sorteado = sortear()
        sorteado = _numero_jogo(sorteado, "Número sorteado")
        venceu = escolhido == sorteado
        efeito = aposta_centavos * 10 if venceu else -aposta_centavos
        novo_saldo = saldo_atual + efeito
        operation_type = "bet_win" if venceu else "bet_loss"
        outcome = "win" if venceu else "loss"

        _iniciar_idempotencia(conexao, key_hash, user_id, "bet", instante)
        transaction_id = _registrar_transacao(
            conexao, user_id, operation_type, efeito, instante,
            wager_cents=aposta_centavos,
            chosen_number=escolhido,
            drawn_number=sorteado,
            outcome=outcome,
        )
        resultado = ResultadoOperacao(
            transaction_id=transaction_id,
            user_id=user_id,
            operation_type=operation_type,
            delta_cents=efeito,
            balance_cents=novo_saldo,
            amount_cents=aposta_centavos,
            outcome=outcome,
            chosen_number=escolhido,
            drawn_number=sorteado,
        )
        _finalizar_idempotencia(conexao, key_hash, resultado)
    return resultado


def registrar_aposta_caca_niqueis(
    caminho: str | os.PathLike[str],
    user_id: int,
    valor: object,
    sortear_simbolos: Callable[[], object],
    chave_idempotencia: str,
    *,
    ocorrido_em: str | None = None,
) -> ResultadoOperacao:
    """Registra três rolos de gatos em uma transação financeira.

    Cada símbolo deve ser um inteiro de 0 a 10. O sorteador é injetado pelo
    chamador e chamado somente após validar saldo para a derrota e capacidade
    para o prêmio de 120 vezes a aposta. A vitória credita o prêmio sem débito
    prévio; a derrota debita apenas a aposta.
    """
    aposta_centavos = converter_para_centavos(valor)
    if not callable(sortear_simbolos):
        raise ErroValidacao("É necessário fornecer o sorteador de símbolos.")
    key_hash = _hash_chave_idempotencia(user_id, "slot", chave_idempotencia)
    instante = ocorrido_em or _agora_utc()

    with transacao_escrita(caminho) as conexao:
        repetida = _resultado_existente(conexao, key_hash)
        if repetida is not None:
            return repetida
        conta = conexao.execute(
            "SELECT balance_cents FROM accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        if conta is None:
            raise ContaNaoEncontrada("Conta não encontrada.")
        saldo_atual = int(conta["balance_cents"])
        if aposta_centavos > saldo_atual:
            raise SaldoInsuficiente("Saldo insuficiente para cobrir uma eventual derrota.")
        premio_maximo = aposta_centavos * 120
        if premio_maximo > VALOR_MAXIMO_CENTAVOS or saldo_atual + premio_maximo > VALOR_MAXIMO_CENTAVOS:
            raise LimiteFinanceiroExcedido("O saldo não comporta o prêmio máximo permitido.")

        valores = sortear_simbolos()
        if not isinstance(valores, (tuple, list)) or len(valores) != 3:
            raise ErroValidacao("O sorteador deve retornar exatamente três símbolos.")
        simbolos = tuple(_numero_jogo(simbolo, "Símbolo sorteado") for simbolo in valores)
        venceu = simbolos[0] == simbolos[1] == simbolos[2]
        efeito = premio_maximo if venceu else -aposta_centavos
        operation_type = "slot_win" if venceu else "slot_loss"
        outcome = "win" if venceu else "loss"

        _iniciar_idempotencia(conexao, key_hash, user_id, "slot", instante)
        transaction_id = _registrar_transacao(
            conexao, user_id, operation_type, efeito, instante,
            wager_cents=aposta_centavos,
            outcome=outcome,
            slot_symbols=simbolos,
        )
        resultado = ResultadoOperacao(
            transaction_id=transaction_id,
            user_id=user_id,
            operation_type=operation_type,
            delta_cents=efeito,
            balance_cents=saldo_atual + efeito,
            amount_cents=aposta_centavos,
            outcome=outcome,
            slot_symbols=simbolos,
        )
        _finalizar_idempotencia(conexao, key_hash, resultado)
    return resultado


def consultar_historico(
    caminho: str | os.PathLike[str], user_id: int, limite: int = 100
) -> list[dict[str, object]]:
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= 1000:
        raise ErroValidacao("Limite de histórico inválido.")
    conexao = abrir_conexao(caminho)
    try:
        financeiros = [
            {"record_kind": "financial", **dict(row)}
            for row in conexao.execute(
                """SELECT ft.transaction_id, ft.operation_type, ft.delta_cents,
                          ft.occurred_at, d.amount_cents,
                          b.wager_cents, b.chosen_number, b.drawn_number, b.outcome,
                          b.game_type, b.slot_symbol_1, b.slot_symbol_2, b.slot_symbol_3
                   FROM financial_transactions AS ft
                   LEFT JOIN deposits AS d USING(transaction_id)
                   LEFT JOIN bets AS b USING(transaction_id)
                   WHERE ft.user_id = ?
                   ORDER BY ft.occurred_at DESC, ft.transaction_id DESC
                   LIMIT ?""",
                (user_id, limite),
            )
        ]
        legado_restante = max(0, limite - len(financeiros))
        legados = [
            {
                "record_kind": "legacy",
                **dict(row),
                "original_data": json.loads(row["original_data_json"]),
            }
            for row in conexao.execute(
                """SELECT legacy_id, source_file, source_row, history_type,
                          original_data_json, original_datetime
                   FROM legacy_history WHERE user_id = ?
                   ORDER BY source_file, source_row LIMIT ?""",
                (user_id, legado_restante),
            )
        ]
        return financeiros + legados
    finally:
        conexao.close()


def consultar_idempotencia(
    caminho: str | os.PathLike[str],
    user_id: int,
    operation_type: str,
    chave: str,
) -> ResultadoOperacao | None:
    return validar_chave_idempotencia(caminho, user_id, operation_type, chave)


def backup_banco(
    origem: str | os.PathLike[str], destino: str | os.PathLike[str]
) -> Path:
    """Cria backup consistente pelo mecanismo SQLite; recusa sobrescrita."""
    origem_path = _caminho(origem)
    destino_path = _caminho(destino)
    if origem_path == destino_path:
        raise ErroValidacao("Origem e destino do backup devem ser diferentes.")
    if not origem_path.is_file():
        raise FileNotFoundError("Banco de origem não encontrado.")
    if destino_path.exists():
        raise FileExistsError("O destino do backup já existe.")
    destino_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    origem_conn = abrir_conexao(origem_path)
    destino_conn = sqlite3.connect(str(destino_path), timeout=10)
    try:
        origem_conn.backup(destino_conn)
        resultado = destino_conn.execute("PRAGMA quick_check").fetchone()[0]
        if resultado != "ok":
            raise ErroPersistencia("A verificação do backup falhou.")
    finally:
        destino_conn.close()
        origem_conn.close()
    try:
        destino_path.chmod(0o600)
    except OSError:
        pass
    return destino_path


def restaurar_backup(
    backup: str | os.PathLike[str], destino: str | os.PathLike[str]
) -> Path:
    """Restaura para um caminho novo, sem sobrescrever banco existente."""
    return backup_banco(backup, destino)


def verificar_integridade(caminho: str | os.PathLike[str]) -> str:
    conexao = abrir_conexao(caminho)
    try:
        return str(conexao.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conexao.close()
