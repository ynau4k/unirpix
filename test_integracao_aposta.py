import json
import os
import re
import secrets
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from cachelib.file import FileSystemCache

import aposta as modulo_aposta
import controle_saldo as modulo_saldo
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


class TestIntegracaoApostaFlask(unittest.TestCase):
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
                "BET_TOKEN_DB": str(Path(self.tempdir.name) / "tokens.sqlite3"),
            }
        )
        self.client = self.app.test_client()
        self._criar_contas()

    def tearDown(self):
        modulo_saldo.DIRETORIO_DADOS = self.diretorio_dados_original
        self.tempdir.cleanup()

    def _criar_contas(self):
        import cadastro

        self.assertEqual(
            cadastro.cadastrar_usuario("Conta teste A", self.cpf_a, self.senha_a),
            "Cadastro realizado com sucesso!",
        )
        self.assertEqual(
            cadastro.cadastrar_usuario("Conta teste B", self.cpf_b, self.senha_b),
            "Cadastro realizado com sucesso!",
        )
        caminho = modulo_saldo.caminho_dados("cadastro.json")
        usuarios = json.loads(caminho.read_text(encoding="utf-8"))
        usuarios[0]["saldo"] = 40.0
        usuarios[1]["saldo"] = 80.0
        caminho.write_text(json.dumps(usuarios), encoding="utf-8")

    def _token_csrf(self, response):
        match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def _token_aposta(self, response):
        match = re.search(rb'name="aposta_token"[^>]*value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def _login(self, client, cpf, senha):
        pagina = client.get("/")
        return client.post(
            "/",
            data={"csrf_token": self._token_csrf(pagina), "cpf": cpf, "senha": senha},
        )

    def _formulario(self, client):
        pagina = client.get("/roleta")
        return self._token_csrf(pagina), self._token_aposta(pagina)

    def _apostar(self, client, numero, valor, cpf=None, csrf=None, token=None):
        if csrf is None or token is None:
            csrf, token = self._formulario(client)
        dados = {"csrf_token": csrf, "aposta_token": token, "numero": numero, "valor": valor}
        if cpf is not None:
            dados["cpf"] = cpf
        return client.post("/roleta", data=dados), csrf, token

    def _historico(self):
        caminho = modulo_saldo.caminho_dados("apostas.json")
        return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else []

    def test_vitoria_credita_dez_vezes_sem_debito_previo_e_atualiza_tela(self):
        self._login(self.client, self.cpf_a, self.senha_a)
        csrf, token = self._formulario(self.client)

        with patch("aposta.random.randint", return_value=7) as sorteio:
            resposta, _, _ = self._apostar(self.client, "7", "5", csrf=csrf, token=token)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["Location"], "/roleta")
        self.assertEqual(sorteio.call_count, 1)
        self.assertEqual(controle_saldo(self.cpf_a), 90.0)
        historico = self._historico()
        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0]["valor_apostado"], 5.0)
        self.assertEqual(historico[0]["valor_resultado"], 50.0)
        self.assertEqual(historico[0]["resultado"], "Vitória!")
        self.assertEqual(set(historico[0]), {
            "cpf", "valor_apostado", "numero_escolhido", "numero_sorteado",
            "resultado", "valor_resultado", "data_hora",
        })
        datetime.fromisoformat(historico[0]["data_hora"])
        pagina = self.client.get("/roleta").get_data(as_text=True)
        self.assertIn(
            "Vitória!\nNúmero sorteado: 7\nValor apostado: R$ 5.00\nLucro líquido ou perda: R$ 50.00",
            pagina,
        )
        self.assertIn("R$ 90,00", pagina)

    def test_derrota_debita_somente_o_valor_apostado(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("aposta.random.randint", return_value=3) as sorteio:
            resposta, _, _ = self._apostar(self.client, "2", "7")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(sorteio.call_count, 1)
        self.assertEqual(controle_saldo(self.cpf_a), 33.0)
        self.assertEqual(self._historico()[0]["valor_resultado"], -7.0)
        pagina = self.client.get("/roleta").get_data(as_text=True)
        self.assertIn("Você perdeu!", pagina)
        self.assertIn("R$ 33,00", pagina)

    def test_saldo_insuficiente_nao_sorteia_nem_registra(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("aposta.random.randint") as sorteio:
            resposta, _, _ = self._apostar(self.client, "4", "41")

        self.assertEqual(resposta.status_code, 302)
        sorteio.assert_not_called()
        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        self.assertIn("Saldo insuficiente", self.client.get("/roleta").get_data(as_text=True))

    def test_numero_invalido_nao_sorteia_e_invalida_token(self):
        self._login(self.client, self.cpf_a, self.senha_a)
        csrf, token = self._formulario(self.client)

        with patch("aposta.random.randint") as sorteio:
            resposta, _, _ = self._apostar(self.client, "11", "5", csrf=csrf, token=token)
            self._apostar(self.client, "7", "5", csrf=csrf, token=token)

        self.assertEqual(resposta.status_code, 302)
        sorteio.assert_not_called()
        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        pagina = self.client.get("/roleta").get_data(as_text=True)
        self.assertIn("Escolha um número inteiro entre 0 e 10", pagina)

    def test_valores_invalidos_nao_sorteiam_nem_alteram_dados(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("aposta.random.randint") as sorteio:
            for valor in ("0", "-1", "texto", "NaN", "Infinity"):
                with self.subTest(valor=valor):
                    resposta, _, _ = self._apostar(self.client, "5", valor)
                    self.assertEqual(resposta.status_code, 302)

        sorteio.assert_not_called()
        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        self.assertIn("positivo e finito", self.client.get("/roleta").get_data(as_text=True))

    def test_roleta_exige_login_e_csrf(self):
        self.assertEqual(self.client.get("/roleta").status_code, 302)
        pagina_login = self.client.get("/")
        csrf = self._token_csrf(pagina_login)

        with patch("aposta.random.randint") as sorteio:
            sem_login = self.client.post(
                "/roleta", data={"csrf_token": csrf, "numero": "5", "valor": "2"}
            )
            self.assertEqual(sem_login.status_code, 302)
            self._login(self.client, self.cpf_a, self.senha_a)
            csrf_roleta, token = self._formulario(self.client)
            sem_csrf = self.client.post(
                "/roleta", data={"aposta_token": token, "numero": "5", "valor": "2"}
            )
            self.assertEqual(sem_csrf.status_code, 400)
            csrf_rejeitado = self.client.post(
                "/roleta",
                data={"csrf_token": csrf_roleta, "aposta_token": token, "numero": "5", "valor": "2"},
            )
            self.assertEqual(csrf_rejeitado.status_code, 302)
            sorteio.assert_not_called()

        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])

    def test_cpf_adulterado_e_ignorado_e_contas_ficam_isoladas(self):
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

        with patch("aposta.random.randint", return_value=10):
            self._apostar(client_a, "10", "3", cpf=self.cpf_b)

        self.assertEqual(controle_saldo(self.cpf_a), 70.0)
        self.assertEqual(controle_saldo(self.cpf_b), 80.0)
        historico = self._historico()
        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0]["cpf"], self.cpf_a)

    def test_falha_ao_gravar_historico_reverte_saldo_e_nao_registra(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("aposta.random.randint", return_value=1), patch(
            "aposta.finalizar_json", side_effect=OSError("falha simulada")
        ):
            self._apostar(self.client, "1", "5")

        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        self.assertIn("saldo foi revertido", self.client.get("/roleta").get_data(as_text=True))

    def test_falha_na_atualizacao_do_saldo_nao_registra_aposta(self):
        self._login(self.client, self.cpf_a, self.senha_a)

        with patch("aposta.random.randint", return_value=1), patch(
            "aposta.atualizar_saldo", return_value=False
        ):
            self._apostar(self.client, "1", "5")

        self.assertEqual(controle_saldo(self.cpf_a), 40.0)
        self.assertEqual(self._historico(), [])
        self.assertIn("Não foi possível atualizar o saldo", self.client.get("/roleta").get_data(as_text=True))

    def test_mesmo_token_de_submissao_nao_processa_a_aposta_duas_vezes(self):
        self._login(self.client, self.cpf_a, self.senha_a)
        csrf, token = self._formulario(self.client)
        dados = {
            "csrf_token": csrf,
            "aposta_token": token,
            "numero": "8",
            "valor": "4",
        }

        with patch("aposta.random.randint", return_value=8) as sorteio:
            primeira = self.client.post("/roleta", data=dados)
            segunda = self.client.post("/roleta", data=dados)

        self.assertEqual(primeira.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(sorteio.call_count, 1)
        self.assertEqual(controle_saldo(self.cpf_a), 80.0)
        self.assertEqual(len(self._historico()), 1)
        self.assertIn("já foi enviado", self.client.get("/roleta").get_data(as_text=True))

    def test_submissoes_concorrentes_com_o_mesmo_token_so_processam_uma_vez(self):
        self._login(self.client, self.cpf_a, self.senha_a)
        csrf, token = self._formulario(self.client)
        cookie = self.client.get_cookie("unirpix_session")
        client_a = self.app.test_client()
        client_b = self.app.test_client()
        client_a.set_cookie("unirpix_session", cookie.value)
        client_b.set_cookie("unirpix_session", cookie.value)
        dados = {
            "csrf_token": csrf,
            "aposta_token": token,
            "numero": "8",
            "valor": "4",
        }

        with patch("aposta.random.randint", return_value=8) as sorteio:
            with ThreadPoolExecutor(max_workers=2) as executor:
                respostas = list(
                    executor.map(
                        lambda client: client.post("/roleta", data=dados),
                        (client_a, client_b),
                    )
                )

        self.assertEqual([resposta.status_code for resposta in respostas], [302, 302])
        self.assertEqual(sorteio.call_count, 1)
        self.assertEqual(controle_saldo(self.cpf_a), 80.0)
        self.assertEqual(len(self._historico()), 1)


if __name__ == "__main__":
    unittest.main()
