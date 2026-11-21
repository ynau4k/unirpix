"""Operação de depósito com serviço reutilizável e interface de terminal."""

import json
import math

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


def registrar_deposito(cpf, valor):
    """Credita o saldo e registra o depósito, sem entrada pelo terminal."""
    valor = _converter_valor(valor)
    if valor is None:
        return "O valor do depósito deve ser positivo e finito."
    if not isinstance(cpf, str) or not cpf:
        return "CPF não encontrado."

    arquivo_depositos = caminho_dados("depositos.json")
    temporario = None

    with LOCK_SALDO:
        try:
            saldo = controle_saldo(cpf)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return "Não foi possível consultar o saldo. Verifique o cadastro."
        if saldo is None:
            return "CPF não encontrado."
        try:
            saldo_valido = (
                not isinstance(saldo, bool)
                and isinstance(saldo, (int, float))
                and math.isfinite(saldo)
                and saldo >= 0
            )
        except (OverflowError, TypeError, ValueError):
            saldo_valido = False
        if not saldo_valido:
            return "O saldo cadastrado é inválido."
        try:
            saldo_atualizado = saldo + valor
        except (OverflowError, TypeError, ValueError):
            return "O saldo não permite calcular este depósito."
        if not math.isfinite(saldo_atualizado):
            return "O depósito excede o limite numérico do saldo."

        try:
            if arquivo_depositos.exists():
                with open(arquivo_depositos, "r", encoding="utf-8") as arquivo:
                    depositos = json.load(arquivo)
            else:
                depositos = []
            if not isinstance(depositos, list):
                raise ValueError("o histórico precisa conter uma lista")
            depositos.append({"cpf": cpf, "valor": valor})
            temporario = preparar_json(arquivo_depositos, depositos)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            remover_temporario(temporario)
            return "Não foi possível preparar o histórico de depósitos. O saldo não foi alterado."

        try:
            atualizado = atualizar_saldo(cpf, valor)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            remover_temporario(temporario)
            return "Não foi possível atualizar o saldo. O depósito não foi registrado."

        if not atualizado:
            remover_temporario(temporario)
            return "CPF não encontrado."

        try:
            finalizar_json(temporario, arquivo_depositos)
            temporario = None
        except OSError:
            try:
                revertido = atualizar_saldo(cpf, -valor)
            except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                revertido = False
            remover_temporario(temporario)
            if revertido:
                return "Não foi possível registrar o depósito; o saldo foi revertido."
            return "Falha ao salvar o depósito e ao reverter o saldo. É necessária conferência manual."

    return "Depósito realizado com sucesso!"


def depositar():
    """Interface de terminal que coleta dados e delega ao serviço."""
    try:
        cpf = input("Digite seu CPF: ")
    except (EOFError, KeyboardInterrupt):
        return "Depósito cancelado."

    while True:
        try:
            entrada = input("Digite o valor do depósito: R$ ")
        except (EOFError, KeyboardInterrupt):
            return "Depósito cancelado."
        valor = _converter_valor(entrada)
        if valor is not None:
            break
        print("O valor do depósito deve ser positivo e finito.")

    return registrar_deposito(cpf, valor)


if __name__ == "__main__":
    print(depositar())
