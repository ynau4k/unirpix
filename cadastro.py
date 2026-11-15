
# cadastro_usuario

import json

nome = input("Digite seu nome completo: ")

def digito(base):
    soma = 0
    peso = len(base) + 1

    for n in base:
        soma += int(n) * peso
        peso -= 1

    dv = 11 - (soma % 11)
    return dv if dv < 10 else 0


def cpf_valido(cpf):
    cpf = cpf.replace('.', '').replace('-', '')

    if len(cpf) != 11 or not cpf.isdigit():
        return False

    if len(set(cpf)) == 1:
        return False

    d1 = digito(cpf[:9])
    d2 = digito(cpf[:9] + str(d1))

    return cpf[9:] == f'{d1}{d2}'


cpf = input("CPF: ")

while True:
    if cpf_valido(cpf):
        print("CPF válido!")
        break
    else:
        print("CPF inválido!")
    cpf = input("CPF: ")

# 1. Limpar o CPF
cpf = cpf.replace('.', '').replace('-', '')

# 2. Abrir o arquivo JSON e recuperar os usuários
try:
    with open("cadastro.json", "r", encoding="utf-8") as arquivo:
        usuarios = json.load(arquivo)

except FileNotFoundError:
    usuarios = []

# 3. Verificar se o CPF já está cadastrado
cpf_existente = any(usuario["cpf"] == cpf for usuario in usuarios)

if cpf_existente:
    print("CPF já cadastrado!")

else:
    # 4. Criar o cadastro do usuário
    usuario = {
        "nome": nome,
        "cpf": cpf,
        "saldo": 0
    }

    usuarios.append(usuario)

    # 5. Salvar os dados no arquivo JSON
    with open("cadastro.json", "w", encoding="utf-8") as arquivo:
        json.dump(usuarios, arquivo, indent=4, ensure_ascii=False)

    print("Cadastro realizado com sucesso!")