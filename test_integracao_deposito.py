import json
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cachelib.file import FileSystemCache

import controle_saldo as modulo_saldo
import deposito as modulo_deposito
from controle_saldo import controle_saldo


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


class TestIntegracaoDepositoFlask(unittest.TestCase):
    cpf_a = "11144477735"
    cpf_b = "22457787823"
    senha_a = "senha-temporaria-da-conta-a"
    senha_b = "senha-temporaria-da-conta-b"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.diretorio_dados_original = modulo_saldo.DIRETORIO_DADOS
        self.dados = Path(self.tempdir.name) / "dados"
        self.dados.mkdir()
        modulo_saldo.DIRETORIO_DADOS = self.dados
        self.sessoes = Path(self.tempdir.name) / "sessoes"
        self.sessoes.mkdir(mode=0o700)
        self.app = create_app(
            {
                "SECRET_KEY": secrets.token_urlsafe(48),
                "TESTING": True,
                "SESSION_CACHELIB": FileSystemCache(
                    cache_dir=str(self.sessoes), threshold=100, mode=0o600
                ),
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()
        self._gravar_usuarios()

    def tearDown(self):
        modulo_saldo.DIRETORIO_DADOS = self.diretorio_dados_original
        self.tempdir.cleanup()

    def _gravar_usuarios(self):
        import cadastro

        self.assertEqual(
            cadastro.cadastrar_usuario("Conta de teste A", self.cpf_a, self.senha_a),
            "Cadastro realizado com sucesso!",
        )
        self.assertEqual(
            cadastro.cadastrar_usuario("Conta de teste B", self.cpf_b, self.senha_b),
            "Cadastro realizado com sucesso!",
        )
        caminho = modulo_saldo.caminho_dados("cadastro.json")
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        dados[0]["saldo"] = 40.0
        dados[1]["saldo"] = 80.0
        caminho.write_text(json.dumps(dados), encoding="utf-8")

    def _token(self, response):
        token = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
        self.assertIsNotNone(token)
        return token.group(1).decode("ascii")

    def _login(self, client, cpf, senha):
        pagina = client.get("/")
        return client.post(
            "/",
            data={"csrf_token": self._token(pagina), "cpf": cpf, "senha": senha},
        )

    def _token_deposito(self, client):
        return self._token(client.get("/deposito"))

    def _post_deposito(self, client, valor, **campos):
        return client.post(
            "/deposito",
            data={"csrf_token": self._token_deposito(client), "valor": valor, **campos},
        )

    def _historico(self):
        caminho = modulo_saldo.caminho_dados("depositos.json")
        return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else []

    def test_saldo_exibido_e_isolado_por_usuario(self):
        client_a = self.app.test_client()
        client_b = self.app.test_client()
        self._login(client_a, self.cpf_a, self.senha_a)
        self._login(client_b, self.cpf_b, self.senha_b)

        inicio_a = client_a.get(f"/inicio?cpf={self.cpf_b}").get_data(as_text=True)
        inicio_b = client_b.get(f"/inicio?cpf={self.cpf_a}").get_data(as_text=True)

        self.assertIn("R$ 40,00", inicio_a)
        self.assertNotIn("R$ 80,00", inicio_a)
        self.assertIn("R$ 80,00", inicio_b)
        self.assertNotIn("R$ 40,00", inicio_b)

    def test_deposito_valido_atualiza_saldo_e_historico_e_redireciona(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        resposta = self._post_deposito(self.client, "12.50")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["Location"], "/deposito")
        self.assertEqual(controle_saldo(self.cpf_a), 52.5)
        self.assertEqual(self._historico(), [{"cpf": self.cpf_a, "valor": 12.5}])
        pagina = self.client.get("/deposito").get_data(as_text=True)
        self.assertIn("Depósito realizado com sucesso!", pagina)
        self.assertIn("R$ 52,50", pagina)

    def test_valores_invalidos_nao_alteram_saldo_ou_historico(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        for valor in ("0", "-1", "texto", "NaN", "Infinity"):
            with self.subTest(valor=valor):
                resposta = self._post_deposito(self.client, valor)
                self.assertEqual(resposta.status_code, 302)
                self.assertIn("positivo e finito", self.client.get("/deposito").get_data(as_text=True))

        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])

    def test_deposito_exige_login_e_csrf(self):
        self.assertEqual(self.client.get("/deposito").status_code, 302)
        pagina_login = self.client.get("/")
        token = self._token(pagina_login)
        sem_login = self.client.post(
            "/deposito", data={"csrf_token": token, "valor": "10"}
        )
        self.assertEqual(sem_login.status_code, 302)
        self.assertEqual(self.client.post("/deposito", data={"valor": "10"}).status_code, 400)
        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])

    def test_cpf_enviado_no_formulario_nao_muda_a_conta_da_sessao(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        self._post_deposito(self.client, "7.25", cpf=self.cpf_b)

        self.assertEqual(controle_saldo(self.cpf_a), 47.25)
        self.assertEqual(controle_saldo(self.cpf_b), 80.0)
        self.assertEqual(self._historico(), [{"cpf": self.cpf_a, "valor": 7.25}])

    def test_falha_ao_atualizar_saldo_nao_registra_historico(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("deposito.atualizar_saldo", return_value=False):
            self._post_deposito(self.client, "10")

        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        self.assertIn("CPF não encontrado", self.client.get("/deposito").get_data(as_text=True))

    def test_falha_ao_gravar_historico_reverte_saldo_e_mostra_erro(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("deposito.finalizar_json", side_effect=OSError("falha simulada")):
            self._post_deposito(self.client, "10")

        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        pagina = self.client.get("/deposito").get_data(as_text=True)
        self.assertIn("saldo foi revertido", pagina)


if __name__ == "__main__":
    unittest.main()
