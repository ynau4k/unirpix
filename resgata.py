import json
import os
from controle_saldo import controle_saldo, atualizar_saldo


def resgatar():
    cpf = input("Digite seu CPF: ")
    valor = float(input("Digite o valor que deseja resgatar: R$ "))

    if valor <= 0:
        return "O valor do resgate deve ser positivo."

    saldo = controle_saldo(cpf)

    if saldo is None:
        return "CPF não encontrado."

    if valor > saldo:
        return "Saldo insuficiente."

    resultado = atualizar_saldo(cpf, -valor)

    if not resultado:
        return "Não foi possível atualizar o saldo."

    arquivo_saques = "saques.json"

    if os.path.exists(arquivo_saques):
        with open(arquivo_saques, "r", encoding="utf-8") as arquivo:
            saques = json.load(arquivo)
    else:
        saques = []

    saque = {
        "cpf": cpf,
        "valor": valor
    }

    saques.append(saque)

    with open(arquivo_saques, "w", encoding="utf-8") as arquivo:
        json.dump(saques, arquivo, indent=4, ensure_ascii=False)

    return "Resgate realizado com sucesso!"


# Executar o resgate
resultado = resgatar()
print(resultado)