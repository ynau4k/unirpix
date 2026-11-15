"""Aplicação web inicial do UnirPix.

Nesta etapa, o Flask apresenta e valida visualmente o formulário de login.
Ainda não há autenticação nem integração com os módulos financeiros.
"""

import re

from flask import Flask, render_template, request


app = Flask(__name__)


def cpf_valido(cpf: str) -> bool:
    """Verifica o formato e os dígitos verificadores de um CPF brasileiro."""
    numeros = re.sub(r"\D", "", cpf)

    # CPF precisa ter 11 números diferentes de uma sequência repetida.
    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False

    digitos = [int(numero) for numero in numeros]

    # Calcula os dois dígitos verificadores do CPF.
    for posicao in (9, 10):
        peso_inicial = posicao + 1
        soma = sum(
            digitos[indice] * (peso_inicial - indice)
            for indice in range(posicao)
        )
        resto = (soma * 10) % 11
        digito_calculado = 0 if resto == 10 else resto

        if digitos[posicao] != digito_calculado:
            return False

    return True


@app.route("/", methods=["GET", "POST"])
def login():
    """Mostra o formulário e valida os campos sem autenticar o usuário."""
    erros = {}
    mensagem = None

    if request.method == "POST":
        cpf = request.form.get("cpf", "").strip()
        senha = request.form.get("senha", "")

        if not cpf:
            erros["cpf"] = "Informe seu CPF."
        elif not cpf_valido(cpf):
            erros["cpf"] = "Digite um CPF válido, com 11 números."

        if not senha.strip():
            erros["senha"] = "Informe sua senha."

        if not erros:
            # Não salva a senha nem consulta os arquivos de dados do projeto.
            mensagem = "Formulário validado. A autenticação ainda não está habilitada."

    return render_template("login.html", erros=erros, mensagem=mensagem)


if __name__ == "__main__":
    # Modo de desenvolvimento local; o depurador fica desativado por segurança.
    app.run(debug=False)
