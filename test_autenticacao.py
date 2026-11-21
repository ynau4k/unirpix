import json
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cachelib.file import FileSystemCache
from werkzeug.security import check_password_hash

import autenticacao
import cadastro
import controle_saldo as modulo_saldo


_import_sessions = tempfile.TemporaryDirectory()
_old_secret = os.environ.get("UNIRPIX_SECRET_KEY")
_old_session_dir = os.environ.get("UNIRPIX_SESSION_DIR")
os.environ["UNIRPIX_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["UNIRPIX_SESSION_DIR"] = _import_sessions.name
try:
    from app import create_app
finally:
    if _old_secret is None:
        os.environ.pop("UNIRPIX_SECRET_KEY", None)
    else:
        os.environ["UNIRPIX_SECRET_KEY"] = _old_secret
    if _old_session_dir is None:
        os.environ.pop("UNIRPIX_SESSION_DIR", None)
    else:
        os.environ["UNIRPIX_SESSION_DIR"] = _old_session_dir


class TestAutenticacao(unittest.TestCase):
    cpf_a = "11144477735"
    cpf_b = "22457787823"
    senha_a = "senha-teste-a-bem-segura"
    senha_b = "senha-teste-b-bem-segura"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.diretorio_dados_original = modulo_saldo.DIRETORIO_DADOS
        modulo_saldo.DIRETORIO_DADOS = Path(self.tempdir.name) / "dados"
        modulo_saldo.DIRETORIO_DADOS.mkdir()
        self.diretorio_sessoes = Path(self.tempdir.name) / "sessoes"
        self.diretorio_sessoes.mkdir(mode=0o700)
        self.app = create_app(
            {
                "SECRET_KEY": secrets.token_urlsafe(48),
                "TESTING": True,
                "SESSION_CACHELIB": FileSystemCache(
                    cache_dir=str(self.diretorio_sessoes),
                    threshold=100,
                    mode=0o600,
                ),
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        modulo_saldo.DIRETORIO_DADOS = self.diretorio_dados_original
        self.tempdir.cleanup()

    def caminho_cadastro(self):
        return modulo_saldo.caminho_dados("cadastro.json")

    def ler_usuarios(self):
        with open(self.caminho_cadastro(), "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def token_csrf(self, response):
        match = re.search(
            rb'name="csrf_token"[^>]*value="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, "o formulário precisa conter token CSRF")
        return match.group(1).decode("ascii")

    def cadastrar_teste(self, nome, cpf, senha):
        resultado = cadastro.cadastrar_usuario(nome, cpf, senha)
        self.assertEqual(resultado, "Cadastro realizado com sucesso!")

    def logar(self, client, cpf, senha):
        pagina = client.get("/")
        return client.post(
            "/",
            data={"csrf_token": self.token_csrf(pagina), "cpf": cpf, "senha": senha},
        )

    def test_backend_de_sessao_esta_ativo_e_cookie_nao_contem_cpf(self):
        self.cadastrar_teste("Pessoa A", self.cpf_a, self.senha_a)

        resposta = self.logar(self.client, self.cpf_a, self.senha_a)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("CacheLibSessionInterface", type(self.app.session_interface).__name__)
        cookie = self.client.get_cookie("unirpix_session")
        self.assertIsNotNone(cookie)
        self.assertNotIn(self.cpf_a, cookie.value)
        self.assertTrue(any(self.diretorio_sessoes.iterdir()))

    def test_login_exige_credenciais_validas_e_respostas_sao_genericas(self):
        self.cadastrar_teste("Pessoa A", self.cpf_a, self.senha_a)
        pagina = self.client.get("/")
        token = self.token_csrf(pagina)

        errado = self.client.post(
            "/", data={"csrf_token": token, "cpf": self.cpf_a, "senha": "senha-incorreta"}
        )
        desconhecido = self.client.post(
            "/", data={"csrf_token": token, "cpf": self.cpf_b, "senha": self.senha_a}
        )

        self.assertEqual(errado.status_code, 200)
        self.assertEqual(desconhecido.status_code, 200)
        self.assertIn("CPF ou senha inválidos.", errado.get_data(as_text=True))
        self.assertIn("CPF ou senha inválidos.", desconhecido.get_data(as_text=True))
        self.assertFalse(self.client.get("/inicio").status_code == 200)

    def test_inicio_exige_login_e_nao_muda_conta_por_cpf_na_url(self):
        self.cadastrar_teste("Pessoa A", self.cpf_a, self.senha_a)
        self.cadastrar_teste("Pessoa B", self.cpf_b, self.senha_b)
        client_a = self.app.test_client()
        client_b = self.app.test_client()

        sem_login = client_a.get("/inicio")
        self.assertEqual(sem_login.status_code, 302)
        self.logar(client_a, self.cpf_a, self.senha_a)
        self.logar(client_b, self.cpf_b, self.senha_b)

        pagina_a = client_a.get(f"/inicio?cpf={self.cpf_b}").get_data(as_text=True)
        pagina_b = client_b.get(f"/inicio?cpf={self.cpf_a}").get_data(as_text=True)
        self.assertIn("Pessoa A", pagina_a)
        self.assertNotIn("Pessoa B", pagina_a)
        self.assertIn("Pessoa B", pagina_b)
        self.assertNotIn("Pessoa A", pagina_b)
        self.assertNotIn(self.cpf_a, pagina_a)
        self.assertNotIn(self.cpf_b, pagina_b)

    def test_logout_limpa_sessao_e_exige_csrf(self):
        self.cadastrar_teste("Pessoa A", self.cpf_a, self.senha_a)
        self.logar(self.client, self.cpf_a, self.senha_a)
        inicio = self.client.get("/inicio")
        token = self.token_csrf(inicio)

        sem_token = self.client.post("/logout")
        self.assertEqual(sem_token.status_code, 400)
        logout = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(self.client.get("/inicio").status_code, 302)

    def test_cadastro_web_exige_csrf_e_persiste_somente_hash(self):
        sem_token = self.client.post(
            "/cadastro",
            data={"nome": "Pessoa A", "cpf": self.cpf_a, "senha": self.senha_a, "confirmacao": self.senha_a},
        )
        self.assertEqual(sem_token.status_code, 400)

        pagina = self.client.get("/cadastro")
        resposta = self.client.post(
            "/cadastro",
            data={
                "csrf_token": self.token_csrf(pagina),
                "nome": "Pessoa A",
                "cpf": self.cpf_a,
                "senha": self.senha_a,
                "confirmacao": self.senha_a,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        usuario = self.ler_usuarios()[0]
        self.assertEqual(set(usuario), {"nome", "cpf", "saldo", "senha_hash"})
        self.assertEqual(usuario["saldo"], 0)
        self.assertNotIn(self.senha_a, json.dumps(usuario))
        self.assertTrue(check_password_hash(usuario["senha_hash"], self.senha_a))

    def test_formularios_com_csrf_invalido_sao_rejeitados(self):
        self.assertEqual(self.client.post("/").status_code, 400)
        self.assertEqual(self.client.post("/cadastro").status_code, 400)

    def test_chave_secreta_e_obrigatoria_sem_fallback(self):
        with patch.dict(os.environ, {"UNIRPIX_SECRET_KEY": ""}):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_ativacao_legada_adiciona_hash_sem_tocar_saldo(self):
        original = [{"nome": "Conta Antiga", "cpf": self.cpf_a, "saldo": 57.25}]
        with open(self.caminho_cadastro(), "w", encoding="utf-8") as arquivo:
            json.dump(original, arquivo)

        ativada = autenticacao.ativar_senha_conta_antiga(self.cpf_a, self.senha_a)
        usuario = self.ler_usuarios()[0]
        segunda_ativacao = autenticacao.ativar_senha_conta_antiga(self.cpf_a, self.senha_b)

        self.assertTrue(ativada)
        self.assertFalse(segunda_ativacao)
        self.assertEqual(usuario["nome"], original[0]["nome"])
        self.assertEqual(usuario["cpf"], original[0]["cpf"])
        self.assertEqual(usuario["saldo"], original[0]["saldo"])
        self.assertTrue(check_password_hash(usuario["senha_hash"], self.senha_a))


if __name__ == "__main__":
    unittest.main()
