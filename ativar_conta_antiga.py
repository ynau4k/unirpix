"""Ferramenta local para ativação administrativa de contas sem senha."""

import os
from getpass import getpass

from autenticacao import ativar_senha_conta_antiga, senha_valida
from cadastro import cpf_valido


def main():
    if os.environ.get("UNIRPIX_ENABLE_LEGACY_ACTIVATION") != "1":
        print("Ativação administrativa desabilitada.")
        return 1

    try:
        cpf = input("CPF da conta existente: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("Operação cancelada.")
        return 1
    if not cpf_valido(cpf):
        print("Não foi possível ativar a conta.")
        return 1

    try:
        senha = getpass("Defina uma senha (mínimo de 12 caracteres): ")
        confirmacao = getpass("Confirme a senha: ")
    except (EOFError, KeyboardInterrupt):
        print("Operação cancelada.")
        return 1

    if not senha_valida(senha) or senha != confirmacao:
        print("Não foi possível ativar a conta.")
        return 1
    if not ativar_senha_conta_antiga(cpf, senha):
        print("Não foi possível ativar a conta.")
        return 1

    print("Senha definida para a conta existente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
