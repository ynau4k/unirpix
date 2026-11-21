"""Operação de aposta e interface opcional pelo terminal."""

import json
import math
import random
from datetime import datetime

from controle_saldo import (
    LOCK_SALDO,
    atualizar_saldo,
    caminho_dados,
    controle_saldo,
    finalizar_json,
    preparar_json,
    remover_temporario,
)


def _converter_valor(valor):
    if isinstance(valor, bool):
        return None
    try:
        if isinstance(valor, str):
            valor = valor.strip().replace(",", ".")
        convertido = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(convertido) or convertido <= 0:
        return None
    return convertido


def _converter_numero(numero):
    if isinstance(numero, bool):
        return None
    try:
        if isinstance(numero, str):
            numero = int(numero.strip())
    except ValueError:
        return None
    if not isinstance(numero, int) or not 0 <= numero <= 10:
        return None
    return numero


def _ler_valor_aposta():
    """Solicita valor finito e positivo; retorna None se a entrada for encerrada."""
    while True:
        try:
            entrada = input("Digite o valor que deseja apostar: R$ ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        valor = _converter_valor(entrada)
        if valor is not None:
            if not math.isfinite(valor * 10):
                print("O valor da aposta é alto demais para calcular o prêmio.")
                continue
            return valor
        print("Digite um valor positivo e finito.")


def _ler_numero_usuario():
    """Solicita um inteiro de 0 a 10 antes de qualquer sorteio."""
    while True:
        try:
            entrada = input("Escolha um número entre 0 e 10: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        numero = _converter_numero(entrada)
        if numero is not None:
            return numero
        print("Escolha um número inteiro entre 0 e 10.")


def _saldo_valido(saldo):
    try:
        return (
            not isinstance(saldo, bool)
            and isinstance(saldo, (int, float))
            and math.isfinite(saldo)
            and saldo >= 0
        )
    except (OverflowError, TypeError, ValueError):
        return False


def _carregar_historico(caminho):
    if not caminho.exists():
        return []
    with open(caminho, "r", encoding="utf-8") as arquivo:
        historico = json.load(arquivo)
    if not isinstance(historico, list):
        raise ValueError("o histórico precisa conter uma lista de apostas")
    return historico


def registrar_aposta(cpf, valor, numero_usuario):
    """Executa e registra uma aposta sem solicitar dados pelo terminal."""
    if not isinstance(cpf, str) or not cpf.strip():
        return "CPF não encontrado."
    cpf = cpf.strip()

    valor = _converter_valor(valor)
    if valor is None:
        return "O valor da aposta deve ser positivo e finito."
    if not math.isfinite(valor * 10):
        return "O valor da aposta é alto demais para calcular o prêmio."

    numero_usuario = _converter_numero(numero_usuario)
    if numero_usuario is None:
        return "Escolha um número inteiro entre 0 e 10."

    try:
        saldo = controle_saldo(cpf)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "Não foi possível consultar o saldo. Verifique o arquivo de cadastro."
    if saldo is None:
        return "CPF não encontrado."
    if not _saldo_valido(saldo):
        return "O saldo cadastrado é inválido."
    if valor > saldo:
        return "Saldo insuficiente para realizar a aposta."
    try:
        if not math.isfinite(saldo + valor * 10):
            return "O saldo atual não comporta o prêmio com segurança."
    except (OverflowError, TypeError, ValueError):
        return "Não foi possível validar o saldo e o prêmio."

    # A escolha e o valor já foram validados antes deste ponto.
    numero_sorteado = random.randint(0, 10)
    venceu = numero_usuario == numero_sorteado
    resultado = "Vitória!" if venceu else "Você perdeu!"
    valor_resultado = valor * 10 if venceu else -valor
    aposta = {
        "cpf": cpf,
        "valor_apostado": valor,
        "numero_escolhido": numero_usuario,
        "numero_sorteado": numero_sorteado,
        "resultado": resultado,
        "valor_resultado": valor_resultado,
        "data_hora": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    caminho_apostas = caminho_dados("apostas.json")
    temporario = None

    # O lock também é utilizado pelo controle compartilhado de saldo. Ele
    # coordena operações deste processo, sem prometer transações entre processos.
    with LOCK_SALDO:
        try:
            saldo_atual = controle_saldo(cpf)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return "Não foi possível confirmar o saldo atual. A aposta não foi registrada."
        if not _saldo_valido(saldo_atual):
            return "O saldo cadastrado é inválido."
        if not venceu and valor > saldo_atual:
            return "Saldo insuficiente para realizar a aposta."
        try:
            if venceu and not math.isfinite(saldo_atual + valor_resultado):
                return "O saldo atual não comporta o prêmio com segurança."
        except (OverflowError, TypeError, ValueError):
            return "Não foi possível validar o saldo atualizado."

        try:
            historico = _carregar_historico(caminho_apostas)
            historico.append(aposta)
            temporario = preparar_json(caminho_apostas, historico)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            remover_temporario(temporario)
            return "Não foi possível preparar o histórico. O saldo não foi alterado."

        try:
            atualizado = atualizar_saldo(cpf, valor_resultado)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            remover_temporario(temporario)
            return (
                "Não foi possível confirmar a atualização do saldo. "
                "Confira o cadastro antes de tentar novamente; o arquivo pode ter sido parcialmente gravado."
            )
        if not atualizado:
            remover_temporario(temporario)
            return "Não foi possível atualizar o saldo. A aposta não foi registrada."

        try:
            finalizar_json(temporario, caminho_apostas)
            temporario = None
        except OSError:
            try:
                revertido = atualizar_saldo(cpf, -valor_resultado)
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                revertido = False
            remover_temporario(temporario)
            if revertido:
                return "Não foi possível registrar o histórico; o saldo foi revertido."
            return (
                "Falha ao registrar o histórico e ao reverter o saldo. "
                "É necessária uma conferência manual."
            )

    return (
        f"{resultado}\n"
        f"Número sorteado: {numero_sorteado}\n"
        f"Valor apostado: R$ {valor:.2f}\n"
        f"Lucro líquido ou perda: R$ {valor_resultado:.2f}"
    )


def apostar():
    """Interface de terminal que coleta dados e delega ao serviço de aposta."""
    try:
        cpf = input("Digite seu CPF: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "Operação cancelada."

    try:
        saldo = controle_saldo(cpf)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "Não foi possível consultar o saldo. Verifique o arquivo de cadastro."
    if saldo is None:
        return "CPF não encontrado."
    if not _saldo_valido(saldo):
        return "O saldo cadastrado é inválido."

    valor = _ler_valor_aposta()
    if valor is None:
        return "Operação cancelada."
    if valor > saldo:
        return "Saldo insuficiente para realizar a aposta."

    numero_usuario = _ler_numero_usuario()
    if numero_usuario is None:
        return "Operação cancelada."
    return registrar_aposta(cpf, valor, numero_usuario)


if __name__ == "__main__":
    print(apostar())
