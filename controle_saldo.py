"""Leitura e atualização compartilhadas do saldo dos usuários."""

import json
import os
import tempfile
import threading
from pathlib import Path


DIRETORIO_DADOS = Path(__file__).resolve().parent
LOCK_SALDO = threading.RLock()


def caminho_dados(nome_arquivo):
    """Retorna o caminho do arquivo de dados junto ao código do projeto."""
    return DIRETORIO_DADOS / nome_arquivo


def preparar_json(caminho, dados):
    """Escreve um JSON temporário no mesmo diretório do destino."""
    caminho = Path(caminho)
    arquivo_temporario = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=caminho.parent,
        prefix=f".{caminho.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporario = Path(arquivo_temporario.name)
    try:
        with arquivo_temporario:
            json.dump(dados, arquivo_temporario, indent=4, ensure_ascii=False)
            arquivo_temporario.write("\n")
            arquivo_temporario.flush()
            os.fsync(arquivo_temporario.fileno())
    except Exception:
        remover_temporario(temporario)
        raise
    return temporario


def finalizar_json(temporario, destino):
    """Substitui o destino por um JSON já escrito, usando rename atômico."""
    os.replace(temporario, destino)


def remover_temporario(caminho):
    if caminho:
        try:
            Path(caminho).unlink(missing_ok=True)
        except OSError:
            pass


def gravar_json_atomico(caminho, dados):
    temporario = preparar_json(caminho, dados)
    try:
        finalizar_json(temporario, caminho)
    finally:
        remover_temporario(temporario)


def controle_saldo(cpf):
    with LOCK_SALDO:
        with open(caminho_dados("cadastro.json"), "r", encoding="utf-8") as arquivo:
            usuarios = json.load(arquivo)
        for usuario in usuarios:
            if usuario["cpf"] == cpf:
                return usuario["saldo"]
    return None


def atualizar_saldo(cpf, valor):
    with LOCK_SALDO:
        with open(caminho_dados("cadastro.json"), "r", encoding="utf-8") as arquivo:
            usuarios = json.load(arquivo)

        for usuario in usuarios:
            if usuario["cpf"] == cpf:
                usuario["saldo"] += valor
                gravar_json_atomico(caminho_dados("cadastro.json"), usuarios)
                return True

    return False
