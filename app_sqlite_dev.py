"""Aplicação de desenvolvimento UnirPix usando somente persistência SQLite.

Este módulo não abre nem grava os JSON da aplicação legada. A factory exige um
banco já inicializado e validado; o comando direto cria um banco temporário que
é removido ao encerrar o processo.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import tempfile
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path

from cachelib.file import FileSystemCache
from flask import Flask, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_session import Session
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

import persistencia_sqlite as persistencia


csrf = CSRFProtect()
server_session = Session()
_CPF_RE = re.compile(r"\D")


def _cpf_valido(cpf: object) -> bool:
    if not isinstance(cpf, str):
        return False
    numeros = _CPF_RE.sub("", cpf)
    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False
    digitos = [int(numero) for numero in numeros]
    for posicao in (9, 10):
        soma = sum(digitos[indice] * (posicao + 1 - indice) for indice in range(posicao))
        resto = (soma * 10) % 11
        if digitos[posicao] != (0 if resto == 10 else resto):
            return False
    return True


def _dinheiro(centavos: int) -> str:
    valor = abs(int(centavos))
    reais, centavos_finais = divmod(valor, 100)
    reais_pt = f"{reais:,}".replace(",", ".")
    sinal = "-" if centavos < 0 else ""
    return f"{sinal}R$ {reais_pt},{centavos_finais:02d}"


def _cat_coins(centavos: int) -> str:
    valor = abs(int(centavos))
    unidades, fracao = divmod(valor, 100)
    unidades_pt = f"{unidades:,}".replace(",", ".")
    sinal = "−" if centavos < 0 else ""
    return f"{sinal}{unidades_pt},{fracao:02d} Cat's Coins"


def _nome_gato(simbolo: int) -> str:
    return persistencia.CAT_COINS_GATOS[int(simbolo)][0]


def _rodadas_cat_coins(registros: list[dict]) -> list[dict[str, str]]:
    rodadas = []
    for registro in registros:
        if registro.get("operation_type") not in {"slot_win", "slot_pair", "slot_loss"}:
            continue
        simbolos = [registro.get(f"slot_symbol_{indice}") for indice in range(1, 4)]
        if any(not isinstance(simbolo, int) or not 0 <= simbolo <= 10 for simbolo in simbolos):
            continue
        nomes = " · ".join(_nome_gato(simbolo) for simbolo in simbolos)
        rodadas.append({
            "nome": nomes,
            "resultado": str(registro.get("outcome") or "loss"),
            "data": str(registro.get("occurred_at") or "Data não disponível"),
            "aposta": _cat_coins(int(registro.get("wager_cents") or 0)),
            "efeito": _cat_coins(int(registro.get("delta_cents") or 0)),
        })
        if len(rodadas) == 8:
            break
    return rodadas


def _formatar_historico(registros: list[dict]) -> list[dict[str, str]]:
    formatados = []
    for registro in registros:
        if registro.get("record_kind") != "financial":
            continue
        tipo = registro.get("operation_type")
        if tipo == "opening_balance":
            continue
        if tipo == "deposit":
            titulo = "Depósito"
            detalhe = f"Valor depositado: {_dinheiro(registro.get('amount_cents') or 0)}"
        elif tipo == "bet_win":
            titulo = "Aposta vencedora"
            detalhe = (
                f"Número escolhido: {registro.get('chosen_number')} · "
                f"número sorteado: {registro.get('drawn_number')} · "
                f"aposta: {_dinheiro(registro.get('wager_cents') or 0)}"
            )
        elif tipo == "bet_loss":
            titulo = "Aposta perdida"
            detalhe = (
                f"Número escolhido: {registro.get('chosen_number')} · "
                f"número sorteado: {registro.get('drawn_number')} · "
                f"aposta: {_dinheiro(registro.get('wager_cents') or 0)}"
            )
        elif tipo in {"slot_win", "slot_pair", "slot_loss"}:
            simbolos = [registro.get(f"slot_symbol_{indice}") for indice in range(1, 4)]
            nomes = [
                _nome_gato(simbolo) if isinstance(simbolo, int) and 0 <= simbolo <= 10 else "Gato"
                for simbolo in simbolos
            ]
            nome_premiado = registro.get("slot_payout_symbol")
            nome = (
                _nome_gato(nome_premiado)
                if isinstance(nome_premiado, int) and 0 <= nome_premiado <= 10 else None
            )
            multiplicador = registro.get("slot_multiplier")
            outcome = registro.get("outcome")
            titulo = {"triple": "Trinca Cat's Coins", "pair": "Par Cat's Coins", "loss": "Rodada perdida"}.get(outcome, "Rodada Cat's Coins")
            detalhe = f"{' · '.join(nomes)} · aposta: {_cat_coins(registro.get('wager_cents') or 0)}"
            if nome and isinstance(multiplicador, int):
                detalhe += f" · prêmio de {nome}: {multiplicador}×"
                if outcome == "pair":
                    detalhe += " (1/3 do prêmio completo)"
            efeito = _cat_coins(int(registro.get("delta_cents") or 0))
            formatados.append({
                "titulo": titulo,
                "detalhe": detalhe,
                "data": str(registro.get("occurred_at") or "Data não disponível"),
                "efeito": efeito,
            })
            continue
        else:
            continue
        formatados.append({
            "titulo": titulo,
            "detalhe": detalhe,
            "data": str(registro.get("occurred_at") or "Data não disponível"),
            "efeito": _dinheiro(int(registro.get("delta_cents") or 0)),
        })
    return formatados


def create_app(config_override: dict | None = None) -> Flask:
    """Cria o app SQLite sem inicializar banco nem usar caminhos JSON."""
    config = dict(config_override or {})
    secret_key = config.get("SECRET_KEY") or os.environ.get("UNIRPIX_SECRET_KEY")
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise RuntimeError("Defina SECRET_KEY ou UNIRPIX_SECRET_KEY para o app SQLite de desenvolvimento.")
    database_value = config.get("SQLITE_DATABASE")
    if not database_value:
        raise RuntimeError("SQLITE_DATABASE deve apontar para um banco temporário inicializado.")
    database_path = Path(database_value).expanduser().resolve()
    # valida_esquema_banco opera em modo somente leitura e não cria o arquivo.
    persistencia.validar_esquema_banco(database_path)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        SQLITE_DATABASE=str(database_path),
        SESSION_TYPE="cachelib",
        SESSION_PERMANENT=True,
        SESSION_SERIALIZATION_FORMAT="json",
        SESSION_ID_LENGTH=32,
        SESSION_COOKIE_NAME="unirpix_sqlite_dev_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=4),
        SESSION_REFRESH_EACH_REQUEST=False,
        WTF_CSRF_TIME_LIMIT=60 * 60,
    )
    app.config.update(config)
    session_dir = Path(app.config.get("SESSION_DIR") or (app.instance_path + "/sqlite-dev-sessions"))
    session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        session_dir.chmod(0o700)
    except OSError:
        pass
    if "SESSION_CACHELIB" not in config:
        app.config["SESSION_CACHELIB"] = FileSystemCache(
            cache_dir=str(session_dir), threshold=500, default_timeout=4 * 60 * 60, mode=0o600
        )

    server_session.init_app(app)
    csrf.init_app(app)

    def obter_usuario_da_sessao():
        user_id = session.get("user_id")
        version = session.get("credential_version")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            return None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return None
        try:
            usuario = persistencia.consultar_usuario_por_id(
                current_app.config["SQLITE_DATABASE"], user_id
            )
        except (OSError, sqlite3.Error, persistencia.ErroPersistencia):
            return None
        if (
            usuario is None
            or not usuario["ativo"]
            or usuario["credential_version"] != version
        ):
            return None
        return usuario

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            usuario = obter_usuario_da_sessao()
            if usuario is None:
                session.clear()
                return redirect(url_for("login"))
            g.usuario = usuario
            return view(*args, **kwargs)
        return wrapped

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        if request.method == "POST":
            session.pop("deposit_token", None)
            session.pop("bet_token", None)
            session.pop("slot_token", None)
        return "Formulário inválido ou expirado. Atualize a página e tente novamente.", 400

    @app.route("/", methods=["GET", "POST"])
    def login():
        if obter_usuario_da_sessao() is not None:
            return redirect(url_for("inicio"))
        session.clear()
        mensagem = None
        if request.args.get("saiu") == "1":
            mensagem = "Você saiu da sua conta."
        elif request.args.get("cadastro") == "concluido":
            mensagem = "Cadastro concluído. Entre com seu CPF e senha."
        if request.method == "POST":
            cpf = request.form.get("cpf", "")
            senha = request.form.get("senha", "")
            usuario = None
            if _cpf_valido(cpf) and isinstance(senha, str) and senha:
                try:
                    candidato = persistencia.consultar_usuario_por_cpf(
                        current_app.config["SQLITE_DATABASE"], cpf
                    )
                    senha_hash = candidato.get("senha_hash") if candidato else None
                    # Legados sem hash permanecem preservados e nunca são
                    # enviados ao verificador do Werkzeug.
                    if candidato and candidato["ativo"] and isinstance(senha_hash, str):
                        if check_password_hash(senha_hash, senha):
                            usuario = candidato
                except (ValueError, TypeError, OSError, sqlite3.Error, persistencia.ErroPersistencia):
                    usuario = None
            if usuario is None:
                mensagem = "CPF ou senha inválidos."
            else:
                session.clear()
                session["user_id"] = int(usuario["user_id"])
                session["credential_version"] = int(usuario["credential_version"])
                session.permanent = True
                current_app.session_interface.regenerate(session)
                return redirect(url_for("inicio"))
        return render_template("login.html", erros={}, mensagem=mensagem)

    @app.route("/cadastro", methods=["GET", "POST"])
    def cadastro():
        if obter_usuario_da_sessao() is not None:
            return redirect(url_for("inicio"))
        session.clear()
        mensagem = None
        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            cpf = request.form.get("cpf", "").strip()
            senha = request.form.get("senha", "")
            confirmacao = request.form.get("confirmacao", "")
            if (
                not nome or not _cpf_valido(cpf)
                or not isinstance(senha, str) or not 12 <= len(senha) <= 1024
                or not senha.strip() or senha != confirmacao
            ):
                mensagem = "Não foi possível concluir o cadastro. Verifique os dados informados."
            else:
                try:
                    senha_hash = generate_password_hash(senha)
                    persistencia.criar_usuario(
                        current_app.config["SQLITE_DATABASE"], cpf, nome, senha_hash
                    )
                    return redirect(url_for("login", cadastro="concluido"))
                except (persistencia.UsuarioJaExiste, persistencia.ErroValidacao):
                    mensagem = "Não foi possível concluir o cadastro. Verifique os dados informados."
                except (OSError, sqlite3.Error, persistencia.ErroPersistencia):
                    mensagem = "Não foi possível concluir o cadastro agora. Tente novamente."
        return render_template("cadastro.html", mensagem=mensagem)

    @app.route("/inicio")
    @login_required
    def inicio():
        conta = persistencia.consultar_conta(
            current_app.config["SQLITE_DATABASE"], int(g.usuario["user_id"])
        )
        saldo = Decimal(conta["balance_cents"]) / 100 if conta else None
        return render_template(
            "inicio.html", nome_usuario=g.usuario["nome"], saldo=saldo,
            historico_ativo=True,
        )

    @app.route("/jogos")
    @login_required
    def jogos():
        return render_template("jogos.html", nome_usuario=g.usuario["nome"])

    @app.route("/deposito", methods=["GET", "POST"])
    @login_required
    def deposito():
        db = current_app.config["SQLITE_DATABASE"]
        user_id = int(g.usuario["user_id"])
        if request.method == "POST":
            form_token = request.form.get("deposit_token", "")
            session_token = session.pop("deposit_token", None)
            if not isinstance(form_token, str) or not secrets.compare_digest(
                form_token, session_token if isinstance(session_token, str) else ""
            ):
                flash("Este formulário expirou. Atualize a página e tente novamente.", "error")
                return redirect(url_for("deposito"))
            try:
                resultado = persistencia.registrar_deposito(db, user_id, request.form.get("valor", ""), form_token)
                flash(
                    f"Depósito realizado! Valor: {_dinheiro(resultado.amount_cents)} · "
                    f"saldo atual: {_dinheiro(resultado.balance_cents)}",
                    "success",
                )
            except persistencia.ErroValidacao:
                flash("Informe um valor positivo com até duas casas decimais.", "error")
            except persistencia.LimiteFinanceiroExcedido:
                flash("O depósito excede o limite permitido para o saldo.", "error")
            except (OSError, sqlite3.Error, persistencia.ErroPersistencia):
                flash("Não foi possível registrar o depósito. O saldo não foi alterado.", "error")
            return redirect(url_for("deposito"))

        conta = persistencia.consultar_conta(db, user_id)
        saldo = Decimal(conta["balance_cents"]) / 100 if conta else None
        token = persistencia.gerar_chave_idempotencia()
        session["deposit_token"] = token
        return render_template("deposito.html", saldo=saldo, deposit_token=token)

    @app.route("/roleta", methods=["GET", "POST"])
    @login_required
    def roleta():
        db = current_app.config["SQLITE_DATABASE"]
        user_id = int(g.usuario["user_id"])
        if request.method == "POST":
            form_token = request.form.get("aposta_token", "")
            session_token = session.pop("bet_token", None)
            if not isinstance(form_token, str) or not secrets.compare_digest(
                form_token, session_token if isinstance(session_token, str) else ""
            ):
                flash("Este formulário expirou ou já foi enviado. Tente novamente.", "error")
                return redirect(url_for("roleta"))
            try:
                resultado = persistencia.registrar_aposta(
                    db, user_id, request.form.get("valor", ""),
                    request.form.get("numero", ""), lambda: secrets.randbelow(11), form_token,
                )
                if resultado.outcome == "win":
                    texto = (
                        "Vitória!\n"
                        f"Número sorteado: {resultado.drawn_number}\n"
                        f"Valor apostado: {_dinheiro(resultado.amount_cents)}\n"
                        f"Lucro líquido ou perda: +{_dinheiro(resultado.delta_cents)}\n"
                        f"Saldo atualizado: {_dinheiro(resultado.balance_cents)}"
                    )
                    flash(texto, "success")
                else:
                    texto = (
                        "Você perdeu!\n"
                        f"Número sorteado: {resultado.drawn_number}\n"
                        f"Valor apostado: {_dinheiro(resultado.amount_cents)}\n"
                        f"Lucro líquido ou perda: {_dinheiro(resultado.delta_cents)}\n"
                        f"Saldo atualizado: {_dinheiro(resultado.balance_cents)}"
                    )
                    flash(texto, "game-result")
                session["roleta_result"] = {
                    "number": int(resultado.drawn_number),
                    "chosen": int(resultado.chosen_number),
                    "outcome": str(resultado.outcome),
                    "amount_cents": int(resultado.amount_cents),
                    "delta_cents": int(resultado.delta_cents),
                    "balance_cents": int(resultado.balance_cents),
                }
            except persistencia.ErroValidacao:
                flash("Confira o número de 0 a 10 e o valor com até duas casas decimais.", "error")
            except persistencia.SaldoInsuficiente:
                flash("Saldo insuficiente para cobrir uma eventual derrota.", "error")
            except persistencia.LimiteFinanceiroExcedido:
                flash("O saldo não comporta o prêmio máximo. Nenhum sorteio foi realizado.", "error")
            except (OSError, sqlite3.Error, persistencia.ErroPersistencia):
                flash("Não foi possível concluir a aposta. O saldo não foi alterado.", "error")
            return redirect(url_for("roleta"))

        conta = persistencia.consultar_conta(db, user_id)
        saldo = Decimal(conta["balance_cents"]) / 100 if conta else None
        spin_result = session.pop("roleta_result", None)
        token = persistencia.gerar_chave_idempotencia()
        session["bet_token"] = token
        return render_template(
            "roleta.html", saldo=saldo, aposta_token=token, spin_result=spin_result
        )

    @app.route("/caca-niqueis", methods=["GET", "POST"])
    @login_required
    def caca_niqueis():
        db = current_app.config["SQLITE_DATABASE"]
        user_id = int(g.usuario["user_id"])
        if request.method == "POST":
            resposta_json = request.accept_mimetypes.best == "application/json"
            form_token = request.form.get("aposta_slot_token", "")
            session_token = session.pop("slot_token", None)

            def erro_aposta(mensagem: str, status: int = 400):
                if resposta_json:
                    novo_token = persistencia.gerar_chave_idempotencia()
                    session["slot_token"] = novo_token
                    try:
                        conta_atual = persistencia.consultar_conta(db, user_id)
                    except (OSError, sqlite3.Error, persistencia.ErroPersistencia):
                        conta_atual = None
                    return jsonify({
                        "ok": False,
                        "message": mensagem,
                        "next_token": novo_token,
                        "balance_cents": int(conta_atual["balance_cents"]) if conta_atual else None,
                    }), status
                flash(mensagem, "error")
                return redirect(url_for("caca_niqueis"))

            if not isinstance(form_token, str) or not secrets.compare_digest(
                form_token, session_token if isinstance(session_token, str) else ""
            ):
                return erro_aposta("Este formulário expirou ou já foi enviado. Tente novamente.")
            try:
                resultado = persistencia.registrar_aposta_caca_niqueis(
                    db,
                    user_id,
                    request.form.get("valor", ""),
                    lambda: tuple(
                        persistencia.simbolo_cat_coins_por_sorteio(
                            secrets.randbelow(persistencia.CAT_COINS_TOTAL_PESOS)
                        )
                        for _ in range(3)
                    ),
                    form_token,
                )
                nome_gato = (
                    _nome_gato(resultado.slot_payout_symbol)
                    if resultado.slot_payout_symbol is not None else None
                )
                multiplicador = resultado.slot_multiplier
                spin_result = {
                    "symbols": list(resultado.slot_symbols or ()),
                    "outcome": str(resultado.outcome),
                    "cat_name": nome_gato,
                    "multiplier": int(multiplicador) if multiplicador is not None else None,
                    "amount_cents": int(resultado.amount_cents),
                    "delta_cents": int(resultado.delta_cents),
                    "prize_cents": max(0, int(resultado.delta_cents)),
                    "old_balance_cents": int(resultado.balance_cents - resultado.delta_cents),
                    "balance_cents": int(resultado.balance_cents),
                }
                if resultado.outcome == "triple":
                    titulo = f"Três {nome_gato}!"
                    mensagem = (
                        f"Prêmio completo de {_cat_coins(resultado.delta_cents)} "
                        f"({multiplicador}× a aposta de {_cat_coins(resultado.amount_cents)})."
                    )
                    categoria = "success"
                elif resultado.outcome == "pair":
                    titulo = "Você acertou dois gatos iguais!"
                    mensagem = (
                        f"Par de {nome_gato}: {_cat_coins(resultado.delta_cents)}, "
                        f"equivalente a 1/3 do prêmio completo de "
                        f"{_cat_coins(resultado.amount_cents * int(multiplicador))}."
                    )
                    categoria = "success"
                else:
                    titulo = "Não foi dessa vez!"
                    mensagem = f"Você perdeu {_cat_coins(resultado.amount_cents)}."
                    categoria = "game-result"
                spin_result["title"] = titulo
                spin_result["message"] = mensagem
                if resposta_json:
                    novo_token = persistencia.gerar_chave_idempotencia()
                    session["slot_token"] = novo_token
                    return jsonify({
                        "ok": True,
                        **spin_result,
                        "next_token": novo_token,
                    })
                flash(
                    f"{titulo} {mensagem} Saldo atualizado: {_cat_coins(resultado.balance_cents)}.",
                    categoria,
                )
                session["slot_result"] = spin_result
            except persistencia.ErroValidacao:
                return erro_aposta("Informe um valor positivo com até duas casas decimais.")
            except persistencia.SaldoInsuficiente:
                return erro_aposta("O saldo precisa cobrir o valor da aposta antes do sorteio.")
            except persistencia.LimiteFinanceiroExcedido:
                return erro_aposta("O saldo não comporta o prêmio máximo. Nenhum sorteio foi realizado.")
            except (OSError, sqlite3.Error, persistencia.ErroPersistencia):
                return erro_aposta("Não foi possível concluir a aposta. O saldo não foi alterado.", 503)
            return redirect(url_for("caca_niqueis"))

        conta = persistencia.consultar_conta(db, user_id)
        saldo = Decimal(conta["balance_cents"]) / 100 if conta else None
        spin_result = session.pop("slot_result", None)
        token = persistencia.gerar_chave_idempotencia()
        session["slot_token"] = token
        historico = persistencia.consultar_historico(db, user_id, limite=200)
        return render_template(
            "caca_niqueis.html", saldo=saldo, aposta_token=token, spin_result=spin_result,
            nomes_gatos=[item[0] for item in persistencia.CAT_COINS_GATOS],
            multiplicadores=[item[2] for item in persistencia.CAT_COINS_GATOS],
            probabilidades=[
                (f"{peso // 100}%" if peso % 100 == 0 else f"{peso / 100:g}".replace(".", ",") + "%")
                for _, peso, _ in persistencia.CAT_COINS_GATOS
            ],
            imagens_gatos=[
                url_for(
                    "static",
                    filename=f"imagens/cats-coins/gatos/simbolo-{indice:02d}-transparente.png",
                    v="20261121-1",
                )
                for indice in range(11)
            ],
            ultimas_rodadas=_rodadas_cat_coins(historico),
        )

    @app.route("/historico")
    @login_required
    def historico():
        registros = persistencia.consultar_historico(
            current_app.config["SQLITE_DATABASE"], int(g.usuario["user_id"]), limite=200
        )
        return render_template(
            "historico.html", nome_usuario=g.usuario["nome"],
            registros=_formatar_historico(registros),
        )

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login", saiu="1"))

    return app


def main() -> None:
    """Executa uma instância local descartável em 127.0.0.1:5051 por padrão."""
    with tempfile.TemporaryDirectory(prefix="unirpix-sqlite-dev-") as temp_root:
        root = Path(temp_root)
        database = persistencia.inicializar_banco(root / "unirpix-dev.sqlite3")
        app = create_app({
            "SECRET_KEY": secrets.token_urlsafe(48),
            "SQLITE_DATABASE": str(database),
            "SESSION_DIR": str(root / "sessions"),
        })
        port = int(os.environ.get("UNIRPIX_DEV_PORT", "5051"))
        if not 1024 <= port <= 65535:
            raise RuntimeError("UNIRPIX_DEV_PORT deve estar entre 1024 e 65535.")
        print(f"UnirPix SQLite de desenvolvimento: http://127.0.0.1:{port}")
        print("Banco e sessões são temporários e serão removidos quando o servidor parar.")
        print("Crie uma conta fictícia pela tela de cadastro para começar.")
        app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
