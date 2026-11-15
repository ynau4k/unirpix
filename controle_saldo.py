import json

def controle_saldo(cpf):
    with open("cadastro.json", "r", encoding="utf-8") as arquivo:
        usuarios = json.load(arquivo)
    for usuario in usuarios:
        if usuario["cpf"] == cpf:
            return usuario["saldo"]
    return None

# Atualizar saldo

def atualizar_saldo(cpf, valor):
    with open("cadastro.json", "r", encoding="utf-8") as arquivo:
        usuarios = json.load(arquivo)

    for usuario in usuarios:
        if usuario["cpf"] == cpf:
            usuario["saldo"] += valor

            with open("cadastro.json", "w", encoding="utf-8") as arquivo:
                json.dump(usuarios, arquivo, indent=4, ensure_ascii=False)

            return True

    return False
