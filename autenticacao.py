"""Helpers de autenticação para os registros locais do UnirPix."""

import json
import re

from werkzeug.security import check_password_hash, generate_password_hash

from controle_saldo import LOCK_SALDO, caminho_dados, gravar_json_atomico


TAMANHO_MINIMO_SENHA = 12
TAMANHO_MAXIMO_SENHA = 1024


def senha_valida(senha):
    return (
        isinstance(senha, str)
        and TAMANHO_MINIMO_SENHA <= len(senha) <= TAMANHO_MAXIMO_SENHA
        and bool(senha.strip())
    )


def hash_senha(senha):
    if not senha_valida(senha):
        raise ValueError("A senha não atende aos requisitos mínimos.")
    return generate_password_hash(senha)


def normalizar_cpf(cpf):
    if not isinstance(cpf, str):
        return ""
    return re.sub(r"\D", "", cpf)


def _ler_usuarios():
    with open(caminho_dados("cadastro.json"), "r", encoding="utf-8") as arquivo:
        usuarios = json.load(arquivo)
    if not isinstance(usuarios, list) or any(not isinstance(item, dict) for item in usuarios):
        raise ValueError("O arquivo de cadastro está em formato inválido.")
    return usuarios


def autenticar_usuario(cpf, senha):
    """Retorna apenas os dados públicos da conta se CPF e senha conferirem."""
    cpf_limpo = normalizar_cpf(cpf)
    if len(cpf_limpo) != 11 or not isinstance(senha, str) or not senha:
        return None

    try:
        with LOCK_SALDO:
            usuarios = _ler_usuarios()
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None

    contas = [usuario for usuario in usuarios if usuario.get("cpf") == cpf_limpo]
    if len(contas) != 1:
        return None

    usuario = contas[0]
    senha_hash = usuario.get("senha_hash")
    if not isinstance(senha_hash, str) or not senha_hash:
        return None
    try:
        if not check_password_hash(senha_hash, senha):
            return None
    except (TypeError, ValueError):
        return None

    return {"cpf": cpf_limpo, "nome": usuario.get("nome", "")}


def usuario_da_sessao(cpf):
    """Confirma que a conta da sessão ainda existe e retorna dados mínimos."""
    cpf_limpo = normalizar_cpf(cpf)
    if len(cpf_limpo) != 11:
        return None
    try:
        with LOCK_SALDO:
            usuarios = _ler_usuarios()
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None

    contas = [usuario for usuario in usuarios if usuario.get("cpf") == cpf_limpo]
    if len(contas) != 1:
        return None
    return {"cpf": cpf_limpo, "nome": contas[0].get("nome", "")}


def ativar_senha_conta_antiga(cpf, senha):
    """Define uma senha somente para uma conta existente ainda sem hash."""
    cpf_limpo = normalizar_cpf(cpf)
    if len(cpf_limpo) != 11 or not senha_valida(senha):
        return False

    try:
        novo_hash = hash_senha(senha)
    except ValueError:
        return False

    caminho_cadastro = caminho_dados("cadastro.json")
    with LOCK_SALDO:
        try:
            usuarios = _ler_usuarios()
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return False

        contas = [usuario for usuario in usuarios if usuario.get("cpf") == cpf_limpo]
        if len(contas) != 1:
            return False
        usuario = contas[0]
        if usuario.get("senha_hash"):
            return False

        usuario["senha_hash"] = novo_hash
        try:
            gravar_json_atomico(caminho_cadastro, usuarios)
        except (OSError, TypeError, ValueError):
            return False
    return True
