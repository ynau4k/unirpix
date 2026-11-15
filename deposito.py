import json
import os
from controle_saldo import atualizar_saldo


def depositar():
    cpf = input("Digite seu CPF: ")
    valor = float(input("Digite o valor do depósito: R$ "))

    if valor <= 0:
        return "O valor do depósito deve ser positivo."

    resultado = atualizar_saldo(cpf, valor)

    if not resultado:
        return "CPF não encontrado."

    arquivo_depositos = "depositos.json"

    if os.path.exists(arquivo_depositos):
        with open(arquivo_depositos, "r", encoding="utf-8") as arquivo:
            depositos = json.load(arquivo)
    else:
        depositos = []

    deposito = {
        "cpf": cpf,
        "valor": valor
    }

    depositos.append(deposito)

    with open(arquivo_depositos, "w", encoding="utf-8") as arquivo:
        json.dump(depositos, arquivo, indent=4, ensure_ascii=False)

    return "Depósito realizado com sucesso!"


# Executar o depósito
resultado = depositar()
print(resultado)