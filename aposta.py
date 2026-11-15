import json
import os
import random
from controle_saldo import controle_saldo, atualizar_saldo


def apostar():
    cpf = input("Digite seu CPF: ")

    saldo = controle_saldo(cpf)

    if saldo is None:
        return "CPF não encontrado."

    valor = float(input("Digite o valor que deseja apostar: R$ "))

    if valor <= 0:
        return "O valor da aposta deve ser positivo."

    if valor > saldo:
        return "Saldo insuficiente para realizar a aposta."

    numero_sorteado = random.randint(0, 10)

    numero_usuario = int(input("Escolha um número entre 0 e 10: "))

    if numero_usuario < 0 or numero_usuario > 10:
        return "Escolha um número entre 0 e 10."

    if numero_usuario == numero_sorteado:
        premio = valor * 10
        atualizar_saldo(cpf, premio)
        resultado = "Vitória!"
        valor_resultado = premio
    else:
        atualizar_saldo(cpf, -valor)
        resultado = "Você perdeu!"
        valor_resultado = -valor

    arquivo_apostas = "apostas.json"

    if os.path.exists(arquivo_apostas):
        with open(arquivo_apostas, "r", encoding="utf-8") as arquivo:
            apostas = json.load(arquivo)
    else:
        apostas = []

    aposta = {
        "cpf": cpf,
        "valor_apostado": valor,
        "numero_escolhido": numero_usuario,
        "numero_sorteado": numero_sorteado,
        "resultado": resultado,
        "valor_resultado": valor_resultado
    }

    apostas.append(aposta)

    with open(arquivo_apostas, "w", encoding="utf-8") as arquivo:
        json.dump(apostas, arquivo, indent=4, ensure_ascii=False)

    return (
        f"{resultado}\n"
        f"Número sorteado: {numero_sorteado}\n"
        f"Valor apostado: R$ {valor:.2f}\n"
        f"Valor do prêmio ou perda: R$ {valor_resultado:.2f}"
    )


# Executar a aposta
resultado = apostar()
print(resultado)