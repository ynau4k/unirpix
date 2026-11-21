"""Aplicação web do UnirPix com autenticação e páginas iniciais."""

import hashlib
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from cachelib.file import FileSystemCache
from flask import Flask, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_session import Session
from flask_wtf.csrf import CSRFError, CSRFProtect

from aposta import registrar_aposta
from autenticacao import autenticar_usuario, senha_valida, usuario_da_sessao
from cadastro import cadastrar_usuario
from controle_saldo import controle_saldo
from deposito import registrar_deposito


csrf = CSRFProtect()
server_session = Session()


def cpf_valido(cpf: str) -> bool:
    if not isinstance(cpf, str):
        return False
    numeros = re.sub(r"\D", "", cpf)
    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False

    digitos = [int(numero) for numero in numeros]
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


def create_app(config_override=None):
    """Cria o aplicativo e exige uma chave secreta fornecida pelo ambiente."""
    config_override = config_override or {}
    secret_key = config_override.get("SECRET_KEY") or os.environ.get("UNIRPIX_SECRET_KEY")
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise RuntimeError(
            "Configure UNIRPIX_SECRET_KEY com pelo menos 32 caracteres antes de iniciar o UnirPix."
        )

    app = Flask(__name__)
    secure_cookie = os.environ.get("UNIRPIX_COOKIE_SECURE", "0").lower() in {
        "1",
        "true",
        "yes",
    }
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_TYPE="cachelib",
        SESSION_PERMANENT=True,
        SESSION_SERIALIZATION_FORMAT="json",
        SESSION_ID_LENGTH=32,
        SESSION_COOKIE_NAME="unirpix_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=secure_cookie,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_REFRESH_EACH_REQUEST=False,
        # Flask-WTF expects this limit in seconds, unlike Flask session lifetime.
        WTF_CSRF_TIME_LIMIT=60 * 60,
    )
    app.config.update(config_override)
    if "BET_TOKEN_DB" not in config_override:
        token_store_dir = Path(
            os.environ.get(
                "UNIRPIX_SESSION_DIR",
                Path(app.instance_path) / "sessions",
            )
        )
        app.config["BET_TOKEN_DB"] = str(token_store_dir / "aposta_tokens.sqlite3")

    if "SESSION_CACHELIB" not in config_override:
        session_directory = Path(
            os.environ.get(
                "UNIRPIX_SESSION_DIR",
                Path(app.instance_path) / "sessions",
            )
        )
        session_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            session_directory.chmod(0o700)
        except OSError:
            # A plataforma pode não permitir chmod; o backend continua isolado
            # na pasta instance, que deve ter permissões restritas no servidor.
            pass
        app.config["SESSION_CACHELIB"] = FileSystemCache(
            cache_dir=str(session_directory),
            threshold=500,
            default_timeout=int(app.config["PERMANENT_SESSION_LIFETIME"].total_seconds()),
            mode=0o600,
        )

    server_session.init_app(app)
    csrf.init_app(app)
    token_claim_lock = threading.Lock()

    def claim_bet_token(token):
        """Claim one form token durably before allowing the game to run."""
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        database_path = Path(current_app.config["BET_TOKEN_DB"])
        database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            database_path.parent.chmod(0o700)
        except OSError:
            pass

        # SQLite's write transaction and unique key serialize claims across
        # threads/processes on this host. The Python lock also protects the
        # current in-process SQLite connection workflow.
        with token_claim_lock:
            database = None
            try:
                database = sqlite3.connect(str(database_path), timeout=10)
                database.execute(
                    "CREATE TABLE IF NOT EXISTS used_bet_tokens ("
                    "token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)"
                )
                database.execute("BEGIN IMMEDIATE")
                now = int(time.time())
                database.execute(
                    "DELETE FROM used_bet_tokens WHERE expires_at <= ?", (now,)
                )
                try:
                    database.execute(
                        "INSERT INTO used_bet_tokens (token_hash, expires_at) VALUES (?, ?)",
                        (token_digest, now + 12 * 60 * 60),
                    )
                except sqlite3.IntegrityError:
                    database.rollback()
                    return False
                database.commit()
                try:
                    database_path.chmod(0o600)
                except OSError:
                    pass
                return True
            except (OSError, sqlite3.Error):
                return False
            finally:
                if database is not None:
                    database.close()

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        if request.path == "/roleta" and request.method == "POST":
            session.pop("aposta_token", None)
        return "Formulário inválido ou expirado. Atualize a página e tente novamente.", 400

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            cpf_sessao = session.get("cpf")
            usuario = usuario_da_sessao(cpf_sessao)
            if usuario is None:
                session.clear()
                return redirect(url_for("login"))
            g.usuario = usuario
            return view(*args, **kwargs)

        return wrapped

    @app.route("/", methods=["GET", "POST"])
    def login():
        if session.get("cpf"):
            return redirect(url_for("inicio"))

        mensagem = None
        if request.args.get("saiu") == "1":
            mensagem = "Você saiu da sua conta."
        elif request.args.get("cadastro") == "concluido":
            mensagem = "Cadastro concluído. Entre com seu CPF e senha."
        erros = {}
        if request.method == "POST":
            cpf = request.form.get("cpf", "").strip()
            senha = request.form.get("senha", "")
            usuario = autenticar_usuario(cpf, senha) if cpf_valido(cpf) else None
            if usuario is None:
                # Uma resposta idêntica evita revelar se a conta existe ou tem senha.
                mensagem = "CPF ou senha inválidos."
            else:
                session.clear()
                session["cpf"] = usuario["cpf"]
                session.permanent = True
                current_app.session_interface.regenerate(session)
                return redirect(url_for("inicio"))

        return render_template("login.html", erros=erros, mensagem=mensagem)

    @app.route("/cadastro", methods=["GET", "POST"])
    def cadastro():
        if session.get("cpf"):
            return redirect(url_for("inicio"))

        mensagem = None
        if request.method == "POST":
            nome = request.form.get("nome", "")
            cpf = request.form.get("cpf", "").strip()
            senha = request.form.get("senha", "")
            confirmacao = request.form.get("confirmacao", "")

            if (
                not nome.strip()
                or not cpf_valido(cpf)
                or not senha_valida(senha)
                or senha != confirmacao
            ):
                mensagem = "Não foi possível concluir o cadastro. Verifique os dados informados."
            else:
                resultado = cadastrar_usuario(nome, cpf, senha)
                if resultado == "Cadastro realizado com sucesso!":
                    return redirect(url_for("login", cadastro="concluido"))
                # Não revela se o CPF já existe.
                mensagem = "Não foi possível concluir o cadastro. Verifique os dados informados."

        return render_template("cadastro.html", mensagem=mensagem)

    @app.route("/inicio")
    @login_required
    def inicio():
        try:
            saldo = controle_saldo(g.usuario["cpf"])
            saldo_valido = (
                not isinstance(saldo, bool)
                and isinstance(saldo, (int, float))
                and math.isfinite(saldo)
                and saldo >= 0
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError):
            saldo_valido = False
            saldo = None

        return render_template(
            "inicio.html",
            nome_usuario=g.usuario.get("nome", ""),
            saldo=saldo if saldo_valido else None,
        )

    @app.route("/deposito", methods=["GET", "POST"])
    @login_required
    def deposito():
        if request.method == "POST":
            # A conta sempre vem da sessão validada, nunca do formulário ou URL.
            resultado = registrar_deposito(
                g.usuario["cpf"],
                request.form.get("valor", ""),
            )
            categoria = (
                "success"
                if resultado == "Depósito realizado com sucesso!"
                else "error"
            )
            flash(resultado, categoria)
            return redirect(url_for("deposito"))

        try:
            saldo = controle_saldo(g.usuario["cpf"])
            saldo_valido = (
                not isinstance(saldo, bool)
                and isinstance(saldo, (int, float))
                and math.isfinite(saldo)
                and saldo >= 0
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError):
            saldo_valido = False
            saldo = None

        return render_template("deposito.html", saldo=saldo if saldo_valido else None)

    @app.route("/roleta", methods=["GET", "POST"])
    @login_required
    def roleta():
        if request.method == "POST":
            token_formulario = request.form.get("aposta_token", "")
            token_sessao = session.pop("aposta_token", None)
            token_confere = (
                isinstance(token_formulario, str)
                and isinstance(token_sessao, str)
                and secrets.compare_digest(token_sessao, token_formulario)
            )
            if not token_confere or not claim_bet_token(token_formulario):
                flash("Este formulário já foi enviado ou expirou. Confira o resultado e tente novamente com um novo formulário.", "error")
                return redirect(url_for("roleta"))

            # CPF comes only from the authenticated server-side session.
            resultado = registrar_aposta(
                g.usuario["cpf"],
                request.form.get("valor", ""),
                request.form.get("numero", ""),
            )
            if resultado.startswith("Vitória!"):
                categoria = "success"
            elif resultado.startswith("Você perdeu!"):
                categoria = "game-result"
            else:
                categoria = "error"
            flash(resultado, categoria)
            return redirect(url_for("roleta"))

        try:
            saldo = controle_saldo(g.usuario["cpf"])
            saldo_valido = (
                not isinstance(saldo, bool)
                and isinstance(saldo, (int, float))
                and math.isfinite(saldo)
                and saldo >= 0
            )
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError):
            saldo_valido = False
            saldo = None

        token = secrets.token_urlsafe(32)
        session["aposta_token"] = token
        return render_template(
            "roleta.html",
            saldo=saldo if saldo_valido else None,
            aposta_token=token,
        )

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login", saiu="1"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=False)
