import re
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cachelib.file import FileSystemCache
from werkzeug.security import check_password_hash

import persistencia_sqlite as persistencia
from app_sqlite_dev import create_app


class TestAppSQLiteDev(unittest.TestCase):
    cpf_a = "11144477735"
    cpf_b = "22457787823"
    senha_a = "senha-ficticia-unirpix-a-2026"
    senha_b = "senha-ficticia-unirpix-b-2026"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="unirpix-test-")
        self.root = Path(self.tempdir.name)
        self.database = persistencia.inicializar_banco(self.root / "dev.sqlite3")
        self.sessions = self.root / "sessions"
        self.sessions.mkdir(mode=0o700)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": secrets.token_urlsafe(48),
            "SQLITE_DATABASE": str(self.database),
            "SESSION_DIR": str(self.sessions),
            "SESSION_CACHELIB": FileSystemCache(
                cache_dir=str(self.sessions), threshold=100, mode=0o600
            ),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def _campo(response, name):
        pattern = rb'name="' + re.escape(name.encode()) + rb'"[^>]*value="([^"]*)"'
        match = re.search(pattern, response.data)
        if not match:
            raise AssertionError(f"Campo oculto {name} não encontrado")
        return match.group(1).decode()

    def _csrf(self, response):
        return self._campo(response, "csrf_token")

    def _signup(self, cpf=None, name="Pessoa de Teste", password=None, client=None):
        client = client or self.client
        cpf = cpf or self.cpf_a
        password = password or self.senha_a
        page = client.get("/cadastro")
        return client.post("/cadastro", data={
            "csrf_token": self._csrf(page),
            "nome": name,
            "cpf": cpf,
            "senha": password,
            "confirmacao": password,
        })

    def _login(self, cpf=None, password=None, client=None):
        client = client or self.client
        page = client.get("/")
        return client.post("/", data={
            "csrf_token": self._csrf(page),
            "cpf": cpf or self.cpf_a,
            "senha": password or self.senha_a,
        })

    def _deposit(self, amount, client=None, cpf_override=None, csrf=None, token=None):
        client = client or self.client
        if csrf is None or token is None:
            page = client.get("/deposito")
            csrf, token = self._csrf(page), self._campo(page, "deposit_token")
        data = {"csrf_token": csrf, "deposit_token": token, "valor": amount}
        if cpf_override:
            data["cpf"] = cpf_override
        return client.post("/deposito", data=data)

    def _bet(self, number, amount, client=None, cpf_override=None, csrf=None, token=None):
        client = client or self.client
        if csrf is None or token is None:
            page = client.get("/roleta")
            csrf, token = self._csrf(page), self._campo(page, "aposta_token")
        data = {"csrf_token": csrf, "aposta_token": token, "numero": number, "valor": amount}
        if cpf_override:
            data["cpf"] = cpf_override
        return client.post("/roleta", data=data)

    def _slot(self, amount, client=None, cpf_override=None, csrf=None, token=None):
        client = client or self.client
        if csrf is None or token is None:
            page = client.get("/caca-niqueis")
            csrf = self._csrf(page)
            token = self._campo(page, "aposta_slot_token")
        data = {"csrf_token": csrf, "aposta_slot_token": token, "valor": amount}
        if cpf_override:
            data["cpf"] = cpf_override
        return client.post("/caca-niqueis", data=data)

    def _balance(self, user_id):
        return persistencia.consultar_conta(self.database, user_id)["balance_cents"]

    def test_factory_recusa_banco_ausente_sem_criar_arquivo(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            create_app({"SECRET_KEY": secrets.token_urlsafe(48), "SQLITE_DATABASE": str(missing)})
        self.assertFalse(missing.exists())

    def test_cadastro_login_hash_e_sessao_server_side(self):
        response = self._signup(name="Conta Fictícia A")
        self.assertEqual(response.status_code, 302)
        account = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self.assertIsNotNone(account)
        self.assertTrue(check_password_hash(account["senha_hash"], self.senha_a))
        self.assertNotIn(self.senha_a, account["senha_hash"])

        response = self._login()
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as stored:
            self.assertEqual(stored["user_id"], account["user_id"])
            self.assertNotIn("cpf", stored)
        home = self.client.get("/inicio")
        self.assertIn("Conta Fictícia A", home.get_data(as_text=True))
        self.assertIn("R$ 0,00", home.get_data(as_text=True))

    def test_cadastro_cpf_duplicado_e_login_invalido(self):
        self._signup()
        duplicate = self._signup(name="Outra pessoa")
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn("Não foi possível concluir", duplicate.get_data(as_text=True))
        invalid = self._login(self.cpf_a, "senha errada")
        self.assertEqual(invalid.status_code, 200)
        self.assertIn("CPF ou senha inválidos", invalid.get_data(as_text=True))

    def test_conta_legada_sem_hash_nao_autentica(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO users(cpf, nome, senha_hash) VALUES (?, ?, NULL)",
                ("39053344705", "Conta antiga fictícia"),
            )
            connection.commit()
        finally:
            connection.close()
        page = self.client.get("/")
        with patch("app_sqlite_dev.check_password_hash") as verifier:
            response = self.client.post("/", data={
                "csrf_token": self._csrf(page), "cpf": "39053344705", "senha": "qualquer",
            })
        self.assertEqual(response.status_code, 200)
        verifier.assert_not_called()

    def test_deposito_valido_precisao_prg_e_historico(self):
        self._signup()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._login()
        response = self._deposit("12,34")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._balance(user["user_id"]), 1234)
        page = self.client.get("/deposito")
        text = page.get_data(as_text=True)
        self.assertIn("Depósito realizado!", text)
        self.assertIn("R$ 12,34", text)
        self.assertEqual(self.client.get("/inicio").status_code, 200)
        self.assertIn("Ver histórico", self.client.get("/inicio").get_data(as_text=True))
        history = self.client.get("/historico").get_data(as_text=True)
        self.assertIn("Depósito", history)
        self.assertIn("R$ 12,34", history)

    def test_deposito_rejeita_valor_com_mais_de_duas_casas_e_token_repetido(self):
        self._signup()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._login()
        page = self.client.get("/deposito")
        csrf, token = self._csrf(page), self._campo(page, "deposit_token")
        invalid = self._deposit("1,001", csrf=csrf, token=token)
        self.assertEqual(invalid.status_code, 302)
        self.assertEqual(self._balance(user["user_id"]), 0)
        duplicate = self._deposit("8,00", csrf=csrf, token=token)
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(self._balance(user["user_id"]), 0)

    def test_deposito_ignora_cpf_enviado_pelo_navegador(self):
        self._signup(self.cpf_a, "Conta A", self.senha_a)
        self._signup(self.cpf_b, "Conta B", self.senha_b, client=self.app.test_client())
        user_a = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        user_b = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_b)
        self._login()
        self._deposit("5,00", cpf_override=self.cpf_b)
        self.assertEqual(self._balance(user_a["user_id"]), 500)
        self.assertEqual(self._balance(user_b["user_id"]), 0)

    def test_roleta_vitoria_e_derrota_com_regra_financeira_e_historico(self):
        self._signup()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._login()
        self._deposit("20,00")
        with patch("app_sqlite_dev.secrets.randbelow", return_value=6) as draw:
            self._bet("6", "2,00")
        self.assertEqual(draw.call_count, 1)
        self.assertEqual(self._balance(user["user_id"]), 4000)
        text = self.client.get("/roleta").get_data(as_text=True)
        self.assertIn("Vitória!", text)
        self.assertIn("Lucro líquido ou perda: +R$ 20,00", text)

        with patch("app_sqlite_dev.secrets.randbelow", return_value=3) as draw:
            self._bet("2", "3,00")
        self.assertEqual(draw.call_count, 1)
        self.assertEqual(self._balance(user["user_id"]), 3700)
        text = self.client.get("/roleta").get_data(as_text=True)
        self.assertIn("Você perdeu!", text)
        self.assertIn("Lucro líquido ou perda: -R$ 3,00", text)
        history = self.client.get("/historico").get_data(as_text=True)
        self.assertIn("Aposta vencedora", history)
        self.assertIn("Aposta perdida", history)

    def test_menu_escolhe_jogo_e_slot_processa_resultado_no_servidor(self):
        self._signup()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._login()
        self._deposit("2,00")
        home = self.client.get("/inicio").get_data(as_text=True)
        self.assertIn('href="/jogos"', home)
        games = self.client.get("/jogos").get_data(as_text=True)
        self.assertIn('href="/roleta"', games)
        self.assertIn('href="/caca-niqueis"', games)
        page = self.client.get("/caca-niqueis")
        self.assertEqual(page.status_code, 200)
        self.assertIn("gatos-simbolos.svg", page.get_data(as_text=True))
        token = self._campo(page, "aposta_slot_token")

        with patch("app_sqlite_dev.secrets.randbelow", side_effect=[7, 7, 7]) as draw:
            response = self._slot("0,01", csrf=self._csrf(page), token=token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(draw.call_count, 3)
        self.assertEqual(self._balance(user["user_id"]), 320)
        result_page = self.client.get("/caca-niqueis")
        html = result_page.get_data(as_text=True)
        self.assertIn('data-symbol-1="7"', html)
        self.assertIn('data-symbol-2="7"', html)
        self.assertIn('data-symbol-3="7"', html)
        self.assertIn('data-result-outcome="win"', html)
        self.assertIn("Trinca de gatinhos! Você venceu!", html)
        self.assertNotRegex(html, r'name="(?:resultado|simbolos_sorteados)"')
        refreshed = self.client.get("/caca-niqueis").get_data(as_text=True)
        self.assertNotIn("data-symbol-1", refreshed)
        self.assertEqual(self._balance(user["user_id"]), 320)

    def test_slot_derrota_historico_e_cpf_enviado_nao_muda_a_conta(self):
        self._signup(self.cpf_a, "Conta Slot A", self.senha_a)
        self._signup(self.cpf_b, "Conta Slot B", self.senha_b, client=self.app.test_client())
        user_a = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        user_b = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_b)
        self._login()
        self._deposit("5,00", cpf_override=self.cpf_b)
        page = self.client.get("/caca-niqueis")
        with patch("app_sqlite_dev.secrets.randbelow", side_effect=[1, 2, 1]) as draw:
            self._slot("1,25", cpf_override=self.cpf_b, csrf=self._csrf(page), token=self._campo(page, "aposta_slot_token"))
        self.assertEqual(draw.call_count, 3)
        self.assertEqual(self._balance(user_a["user_id"]), 375)
        self.assertEqual(self._balance(user_b["user_id"]), 0)
        result = self.client.get("/caca-niqueis").get_data(as_text=True)
        self.assertIn("Desta vez, os gatinhos não combinaram.", result)
        self.assertIn("Gatos sorteados: 1 · 2 · 1", result)
        history = self.client.get("/historico").get_data(as_text=True)
        self.assertIn("Caça-níqueis perdido", history)
        self.assertIn("Gatos sorteados: 1 · 2 · 1", history)

    def test_slot_exige_login_csrf_e_token_de_uso_unico(self):
        self.assertEqual(self.client.get("/caca-niqueis").status_code, 302)
        self._signup()
        self._login()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._deposit("5,00")
        page = self.client.get("/caca-niqueis")
        token = self._campo(page, "aposta_slot_token")
        with patch("app_sqlite_dev.secrets.randbelow") as draw:
            missing_csrf = self.client.post("/caca-niqueis", data={
                "aposta_slot_token": token, "valor": "1,00",
            })
        self.assertEqual(missing_csrf.status_code, 400)
        draw.assert_not_called()
        self.assertEqual(self._balance(user["user_id"]), 500)

        page = self.client.get("/caca-niqueis")
        csrf, token = self._csrf(page), self._campo(page, "aposta_slot_token")
        with patch("app_sqlite_dev.secrets.randbelow", side_effect=[0, 1, 2]) as draw:
            self._slot("1,00", csrf=csrf, token=token)
            duplicate = self._slot("1,00", csrf=csrf, token=token)
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(draw.call_count, 3)
        self.assertEqual(self._balance(user["user_id"]), 400)

    def test_slot_usuarios_nao_autenticados_nao_acessam_dados(self):
        self.assertEqual(self.client.get("/jogos").status_code, 302)
        page = self.client.get("/")
        self.assertEqual(self.client.post("/caca-niqueis", data={
            "csrf_token": self._csrf(page),
        }).status_code, 302)

    def test_slot_tem_layout_responsivo_e_vista_reduzida(self):
        source = Path("static/css/estilo.css").read_text(encoding="utf-8")
        script = Path("static/js/caca_niqueis.js").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 620px)", source)
        self.assertIn(".slot-reels", source)
        self.assertIn("prefers-reduced-motion", source)
        self.assertIn("prefers-reduced-motion", script)

    def test_resultado_servidor_e_transmitido_sem_expor_campo_de_resultado_no_formulario(self):
        self._signup()
        self._login()
        self._deposit("2,00")
        step = 360 / 11
        for drawn in range(11):
            page = self.client.get("/roleta")
            chosen = (drawn + 1) % 11
            with patch("app_sqlite_dev.secrets.randbelow", return_value=drawn):
                response = self._bet(str(chosen), "0,01", csrf=self._csrf(page), token=self._campo(page, "aposta_token"))
            self.assertEqual(response.status_code, 302)
            result_page = self.client.get("/roleta")
            html = result_page.get_data(as_text=True)
            self.assertIn(f'data-result-number="{drawn}"', html)
            self.assertIn(f'data-chosen-number="{chosen}"', html)
            self.assertIn('data-result-outcome="loss"', html)
            self.assertIn('name="numero"', html)
            self.assertNotRegex(html, r'name="(?:resultado|numero_sorteado)"')

            # The redirect target consumes the one-shot animation result. A
            # normal reload is only a GET and therefore cannot replay a bet.
            refreshed = self.client.get("/roleta").get_data(as_text=True)
            self.assertNotIn("data-result-number", refreshed)

            # The JS landing formula must put this server-selected sector
            # exactly under the pointer (top is -90 degrees in the SVG).
            correction = (360 - drawn * step) % 360
            center_angle = -90 + drawn * step
            self.assertAlmostEqual((center_angle + correction + 90) % 360, 0, places=8)

    def test_aposta_invalida_nao_sorteia_e_saldo_maximo_bloqueia_antes_do_sorteio(self):
        self._signup()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._login()
        self._deposit("10,00")
        with patch("app_sqlite_dev.secrets.randbelow") as draw:
            self._bet("11", "1,00")
            self._bet("2", "1,001")
        draw.assert_not_called()
        self.assertEqual(self._balance(user["user_id"]), 1000)

        self._deposit("999999990,00")
        self.assertEqual(self._balance(user["user_id"]), 100_000_000_000)
        with patch("app_sqlite_dev.secrets.randbelow") as draw:
            self._bet("2", "1,00")
        draw.assert_not_called()
        self.assertEqual(self._balance(user["user_id"]), 100_000_000_000)
        self.assertIn("prêmio máximo", self.client.get("/roleta").get_data(as_text=True))

    def test_aposta_token_repetido_nao_duplica_operacao(self):
        self._signup()
        user = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        self._login()
        self._deposit("10,00")
        page = self.client.get("/roleta")
        csrf, token = self._csrf(page), self._campo(page, "aposta_token")
        with patch("app_sqlite_dev.secrets.randbelow", return_value=4) as draw:
            self._bet("4", "1,00", csrf=csrf, token=token)
            self._bet("4", "1,00", csrf=csrf, token=token)
        self.assertEqual(draw.call_count, 1)
        self.assertEqual(self._balance(user["user_id"]), 2000)
        self.assertEqual(
            sum(row["operation_type"] == "bet_win" for row in persistencia.consultar_historico(
                self.database, user["user_id"]
            )),
            1,
        )

    def test_rotas_protegidas_csrf_logout_e_isolamento_de_contas(self):
        self.assertEqual(self.client.get("/inicio").status_code, 302)
        self.assertEqual(self.client.get("/deposito").status_code, 302)
        self.assertEqual(self.client.get("/roleta").status_code, 302)
        self.assertEqual(self.client.get("/historico").status_code, 302)
        self._signup(self.cpf_a, "Conta A", self.senha_a)
        other = self.app.test_client()
        self._signup(self.cpf_b, "Conta B", self.senha_b, client=other)
        user_a = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        user_b = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_b)
        self._login()
        self._deposit("4,00")
        self._login(self.cpf_b, self.senha_b, client=other)
        other_deposit = self._deposit("17,00", client=other)
        self.assertEqual(other_deposit.status_code, 302)
        page_a = self.client.get("/historico?cpf=" + self.cpf_b).get_data(as_text=True)
        self.assertIn("R$ 4,00", page_a)
        self.assertNotIn("R$ 17,00", page_a)
        self.assertEqual(self._balance(user_a["user_id"]), 400)
        self.assertEqual(self._balance(user_b["user_id"]), 1700)

        home = self.client.get("/inicio")
        logout = self.client.post("/logout", data={"csrf_token": self._csrf(home)})
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(self.client.get("/inicio").status_code, 302)

    def test_csrf_ausente_bloqueia_deposito_e_aposta(self):
        self._signup()
        self._login()
        with self.client.session_transaction() as stored:
            user_id = stored["user_id"]
        deposit_page = self.client.get("/deposito")
        deposit_token = self._campo(deposit_page, "deposit_token")
        bet_page = self.client.get("/roleta")
        bet_token = self._campo(bet_page, "aposta_token")
        with patch("app_sqlite_dev.secrets.randbelow") as draw:
            self.assertEqual(self.client.post("/deposito", data={
                "deposit_token": deposit_token, "valor": "1,00",
            }).status_code, 400)
            self.assertEqual(self.client.post("/roleta", data={
                "aposta_token": bet_token, "numero": "1", "valor": "1,00",
            }).status_code, 400)
        draw.assert_not_called()
        self.assertEqual(self._balance(user_id), 0)


if __name__ == "__main__":
    unittest.main()
