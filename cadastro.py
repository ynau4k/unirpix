"""Cadastro de usuários, com interface opcional pelo terminal."""

import json
from getpass import getpass

from autenticacao import hash_senha, senha_valida
from controle_saldo import LOCK_SALDO, caminho_dados, gravar_json_atomico


def digito(base):
    soma = 0
    peso = len(base) + 1

    for numero in base:
        soma += int(numero) * peso
        peso -= 1

    dv = 11 - (soma % 11)
    return dv if dv < 10 else 0


def cpf_valido(cpf):
    if not isinstance(cpf, str):
        return False
    cpf = cpf.replace(".", "").replace("-", "")

    if len(cpf) != 11 or not cpf.isdigit():
        return False
    if len(set(cpf)) == 1:
        return False

    d1 = digito(cpf[:9])
    d2 = digito(cpf[:9] + str(d1))
    return cpf[9:] == f"{d1}{d2}"


def cadastrar_usuario(nome, cpf, senha):
    """Cria um cadastro sem solicitar dados pelo terminal."""
    if not isinstance(nome, str):
        return "Nome inválido."
    if not cpf_valido(cpf):
        return "CPF inválido!"
    if not senha_valida(senha):
        return "A senha deve ter ao menos 12 caracteres e não pode ficar vazia."

    cpf_limpo = cpf.replace(".", "").replace("-", "")
    senha_hash = hash_senha(senha)
    caminho_cadastro = caminho_dados("cadastro.json")

    with LOCK_SALDO:
        try:
            with open(caminho_cadastro, "r", encoding="utf-8") as arquivo:
                usuarios = json.load(arquivo)
        except FileNotFoundError:
            usuarios = []
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "Não foi possível ler o arquivo de cadastro."

        if not isinstance(usuarios, list) or any(
            not isinstance(usuario, dict) or "cpf" not in usuario for usuario in usuarios
        ):
            return "O arquivo de cadastro está em um formato inválido."

        if any(usuario["cpf"] == cpf_limpo for usuario in usuarios):
            return "CPF já cadastrado!"

        usuarios.append(
            {"nome": nome, "cpf": cpf_limpo, "saldo": 0, "senha_hash": senha_hash}
        )
        try:
            gravar_json_atomico(caminho_cadastro, usuarios)
        except (OSError, TypeError, ValueError):
            return "Não foi possível salvar o cadastro."
        return "Cadastro realizado com sucesso!"


def cadastrar():
    """Interface de terminal; mantém as perguntas e mensagens existentes."""
    try:
        nome = input("Digite seu nome completo: ")
        cpf = input("CPF: ")
    except (EOFError, KeyboardInterrupt):
        print("Cadastro cancelado.")
        return "Cadastro cancelado."

    while not cpf_valido(cpf):
        print("CPF inválido!")
        try:
            cpf = input("CPF: ")
        except (EOFError, KeyboardInterrupt):
            print("Cadastro cancelado.")
            return "Cadastro cancelado."

    print("CPF válido!")
    while True:
        try:
            senha = getpass("Crie uma senha (mínimo de 12 caracteres): ")
            confirmacao = getpass("Confirme a senha: ")
        except (EOFError, KeyboardInterrupt):
            print("Cadastro cancelado.")
            return "Cadastro cancelado."
        if not senha_valida(senha):
            print("A senha deve ter ao menos 12 caracteres e não pode ficar vazia.")
            continue
        if senha != confirmacao:
            print("As senhas não coincidem.")
            continue
        break

    resultado = cadastrar_usuario(nome, cpf, senha)
    print(resultado)
    return resultado


if __name__ == "__main__":
    cadastrar()
