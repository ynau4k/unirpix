import concurrent.futures
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash

import persistencia_sqlite as persistencia


class TestPersistenciaSQLite(unittest.TestCase):
    cpf_a = "11144477735"
    cpf_b = "22457787823"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "financeiro.sqlite3"
        persistencia.inicializar_banco(self.database)
        self.user_a = persistencia.criar_usuario(
            self.database, self.cpf_a, "Conta teste A", self._hash_fixture("a")
        )
        self.user_b = persistencia.criar_usuario(
            self.database, self.cpf_b, "Conta teste B", self._hash_fixture("b")
        )

    @staticmethod
    def _hash_fixture(char):
        return f"scrypt:32768:8:1$fixtureSalt000001${char * 128}"

    def tearDown(self):
        self.tempdir.cleanup()

    def _sql(self, sql, params=()):
        connection = persistencia.abrir_conexao(self.database)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def test_inicializacao_configura_esquema_e_chaves_estrangeiras(self):
        connection = persistencia.abrir_conexao(self.database)
        try:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            objects = {
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT type, name FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            connection.close()
        self.assertTrue(
            {
                ("table", "users"), ("table", "accounts"),
                ("table", "financial_transactions"), ("view", "deposits"),
                ("view", "bets"), ("table", "legacy_history"),
                ("table", "idempotency_keys"), ("table", "schema_metadata"),
                ("table", "credential_audit_events"),
            }.issubset(objects)
        )

    def test_inicializacao_recusa_esquema_incompativel_sem_altera_lo(self):
        incompatible = self.root / "incompativel.sqlite3"
        connection = sqlite3.connect(incompatible)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('preservado')")
        connection.commit()
        connection.close()
        before = incompatible.read_bytes()

        with self.assertRaises(persistencia.ErroPersistencia):
            persistencia.inicializar_banco(incompatible)

        self.assertEqual(incompatible.read_bytes(), before)

    def test_criacao_de_esquema_falha_atomicamente_e_pode_ser_retentada(self):
        interrupted = self.root / "interrompido.sqlite3"
        original = persistencia._executar_declaracao_sql
        calls = 0

        def fail_after_some_statements(connection, statement):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise RuntimeError("falha simulada ao criar esquema")
            return original(connection, statement)

        with patch.object(
            persistencia, "_executar_declaracao_sql", side_effect=fail_after_some_statements
        ):
            with self.assertRaises(RuntimeError):
                persistencia.inicializar_banco(interrupted)

        connection = sqlite3.connect(interrupted)
        try:
            user_objects = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(user_objects, [])

        persistencia.inicializar_banco(interrupted)
        persistencia.validar_esquema_banco(interrupted)

    def test_reinicializacao_valida_esquema_sem_recriar_ou_apagar_dados(self):
        balance_before = persistencia.consultar_conta(self.database, self.user_a)["balance_cents"]
        persistencia.inicializar_banco(self.database)
        self.assertEqual(
            persistencia.consultar_conta(self.database, self.user_a)["balance_cents"],
            balance_before,
        )

    def test_inicializacao_recusa_dados_com_violacao_de_check_sem_modifica_los(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "INSERT INTO migration_batches VALUES (?, ?, ?, ?, ?)",
                ("f" * 64, "2026-01-01T00:00:00Z", -1, 0, 0),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(persistencia.ErroPersistencia):
            persistencia.inicializar_banco(self.database)

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute("SELECT account_count FROM migration_batches").fetchone()[0],
                -1,
            )
        finally:
            connection.close()

    def test_inicializacao_detecta_fk_orfa_sem_alterar_o_registro(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            bundle_hash = "e" * 64
            connection.execute(
                "INSERT INTO migration_batches"
                "(bundle_hash,imported_at,account_count,deposit_history_count,bet_history_count)"
                " VALUES (?,'2026-01-01T00:00:00Z',0,1,0)", (bundle_hash,)
            )
            connection.execute(
                "INSERT INTO legacy_history"
                "(bundle_hash,source_file,source_row,history_type,user_id,original_data_json,record_hash)"
                " VALUES (?,'depositos.json',77,'deposit',987654,'{}',?)",
                (bundle_hash, "7" * 64),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(persistencia.ErroPersistencia):
            persistencia.inicializar_banco(self.database)

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM legacy_history WHERE user_id = 987654"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_criacao_de_conta_mantem_hash_e_cpf_unico(self):
        account = persistencia.consultar_conta(self.database, self.user_a)
        self.assertEqual(account["cpf"], self.cpf_a)
        self.assertEqual(account["balance_cents"], 0)
        self.assertEqual(account["senha_hash"], self._hash_fixture("a"))
        self.assertEqual(account["credential_version"], 1)
        self.assertNotIn("senha", account)

        with self.assertRaises(persistencia.UsuarioJaExiste):
            persistencia.criar_usuario(
                self.database, "111.444.777-35", "Outra conta teste", self._hash_fixture("c")
            )
        self.assertEqual(
            self._sql("SELECT COUNT(*) FROM users")[0][0], 2
        )

    def test_versao_e_assinatura_do_esquema_estao_atualizadas(self):
        metadata = self._sql(
            "SELECT schema_version, schema_sha256 FROM schema_metadata WHERE singleton=1"
        )[0]
        self.assertEqual(metadata[0], 6)
        self.assertEqual(
            metadata[1],
            hashlib.sha256(persistencia.ARQUIVO_ESQUEMA.read_bytes()).hexdigest(),
        )
        persistencia.validar_esquema_banco(self.database)

    def test_consultas_de_autenticacao_por_cpf_id_e_estado(self):
        by_cpf = persistencia.consultar_usuario_por_cpf(self.database, self.cpf_a)
        by_id = persistencia.consultar_usuario_por_id(self.database, self.user_a)
        state = persistencia.consultar_estado_credencial(self.database, self.user_a)

        self.assertEqual(by_cpf["user_id"], self.user_a)
        self.assertEqual(by_id["cpf"], self.cpf_a)
        self.assertEqual(by_cpf["credential_version"], 1)
        self.assertTrue(by_cpf["ativo"])
        self.assertEqual(state, {
            "user_id": self.user_a,
            "ativo": True,
            "credential_version": 1,
        })
        self.assertNotIn("balance_cents", by_cpf)
        self.assertNotIn("senha_hash", state)
        self.assertIsNone(persistencia.consultar_usuario_por_id(self.database, 99999))
        self.assertIsNone(persistencia.consultar_estado_credencial(self.database, 99999))

    def test_criacao_recusa_hash_ausente_vazio_plaintext_ou_malformado(self):
        for bad_hash in (None, "", "   ", "senha-plaintext", "scrypt$invalido"):
            with self.subTest(bad_hash=repr(bad_hash)):
                with self.assertRaises(persistencia.ErroValidacao):
                    persistencia.criar_usuario(
                        self.database, "39053344705", "Conta hash inválido", bad_hash
                    )
        self.assertEqual(self._sql("SELECT COUNT(*) FROM users")[0][0], 2)

    def test_hashes_werkzeug_seguros_sao_aceitos_e_verificaveis(self):
        for method in ("scrypt", "pbkdf2:sha256:600000"):
            with self.subTest(method=method):
                encoded = generate_password_hash("SenhaFicticia-2026", method=method)
                self.assertEqual(persistencia._hash_senha_validado(encoded), encoded)
                self.assertTrue(check_password_hash(encoded, "SenhaFicticia-2026"))
                self.assertFalse(check_password_hash(encoded, "senha-errada"))

    def test_hashes_fracos_e_parametros_extremos_sao_recusados(self):
        fracos = (
            f"pbkdf2:sha256:599999$fixtureSalt000001${'a' * 64}",
            f"scrypt:8192:8:1$fixtureSalt000001${'a' * 128}",
            f"scrypt:32768:1:1$fixtureSalt000001${'a' * 128}",
        )
        extremos = (
            f"pbkdf2:sha256:2000001$fixtureSalt000001${'a' * 64}",
            f"scrypt:524288:8:1$fixtureSalt000001${'a' * 128}",
            f"scrypt:32768:8:3$fixtureSalt000001${'a' * 128}",
            f"scrypt:32768:16:1$fixtureSalt000001${'a' * 128}",
        )
        for encoded in fracos + extremos:
            with self.subTest(method=encoded.split("$")[0]):
                with self.assertRaises(persistencia.ErroValidacao):
                    persistencia._hash_senha_validado(encoded)

    def test_conta_legada_com_hash_nulo_permanece_sem_autenticacao(self):
        connection = sqlite3.connect(self.database)
        try:
            cursor = connection.execute(
                "INSERT INTO users(cpf, nome, senha_hash) VALUES (?, ?, NULL)",
                ("39053344705", "Conta legada fictícia"),
            )
            legacy_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()
        result = persistencia.consultar_usuario_por_id(self.database, legacy_id)
        self.assertIsNone(result["senha_hash"])
        self.assertEqual(result["credential_version"], 1)

    def test_schema_recusa_senha_em_claro_hashes_malformados_e_versao_sem_hash(self):
        connection = persistencia.abrir_conexao(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE users SET senha_hash=?, credential_version=2 WHERE user_id=?",
                    ("plaintext-password", self.user_a),
                )
            connection.rollback()
            for malformed in (
                f"scrypt:32768:8:x$fixtureSalt000001${'a' * 128}",
                f"pbkdf2:sha256:600000junk$fixtureSalt000001${'a' * 64}",
                f"pbkdf2:sha256:1000$fixtureSalt000001${'a' * 64}",
                f"scrypt:8192:8:1$fixtureSalt000001${'a' * 128}",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO users(cpf, nome, senha_hash) VALUES (?, ?, ?)",
                        ("39053344705", "Hash SQL inválido", malformed),
                    )
                connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE users SET credential_version=2 WHERE user_id=?", (self.user_a,)
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE users SET senha_hash=? WHERE user_id=?",
                    (self._hash_fixture("9"), self.user_a),
                )
            connection.rollback()
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM credential_audit_events").fetchone()[0],
                0,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO users(cpf, nome, senha_hash) VALUES (?, ?, ?)",
                    ("39053344705", "Senha em claro", "senha-em-claro"),
                )
            connection.rollback()
        finally:
            connection.close()

    def test_atualizacao_incrementa_versao_e_recusa_versao_antiga(self):
        new_hash = self._hash_fixture("d")
        self.assertEqual(
            persistencia.atualizar_hash_senha(
                self.database, self.user_a, new_hash, versao_esperada=1
            ),
            2,
        )
        user = persistencia.consultar_usuario_por_id(self.database, self.user_a)
        self.assertEqual(user["senha_hash"], new_hash)
        self.assertEqual(user["credential_version"], 2)
        with self.assertRaises(persistencia.VersaoCredencialDesatualizada):
            persistencia.atualizar_hash_senha(
                self.database, self.user_a, self._hash_fixture("e"), versao_esperada=1
            )
        with self.assertRaises(persistencia.ContaNaoEncontrada):
            persistencia.atualizar_hash_senha(
                self.database, 99999, self._hash_fixture("e"), versao_esperada=1
            )

    def test_atualizacao_concorrente_com_mesma_versao_so_tem_um_vencedor(self):
        barrier = threading.Barrier(2)

        def update(char):
            barrier.wait(timeout=5)
            try:
                return persistencia.atualizar_hash_senha(
                    self.database,
                    self.user_a,
                    self._hash_fixture(char),
                    versao_esperada=1,
                )
            except persistencia.VersaoCredencialDesatualizada:
                return "stale"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ("d", "e")))
        self.assertEqual(sorted(str(item) for item in results), ["2", "stale"])
        self.assertEqual(
            persistencia.consultar_estado_credencial(self.database, self.user_a)["credential_version"],
            2,
        )

    def test_atualizacao_gera_auditoria_sem_segredos(self):
        new_hash = self._hash_fixture("d")
        self.assertEqual(persistencia.atualizar_hash_senha(self.database, self.user_a, new_hash, 1), 2)
        event = self._sql(
            "SELECT actor_user_id, account_user_id, action, occurred_at, protocol, result, "
            "credential_version_before, credential_version_after FROM credential_audit_events"
        )[0]
        self.assertEqual(tuple(event), (
            self.user_a, self.user_a, "password_change", event[3], "self-service", "success", 1, 2
        ))
        self.assertTrue(event[3].endswith("Z"))
        audit_columns = {row[1] for row in self._sql("PRAGMA table_info(credential_audit_events)")}
        self.assertNotIn("cpf", audit_columns)
        self.assertNotIn("senha_hash", audit_columns)
        rendered_event = repr(tuple(event))
        self.assertNotIn(self.cpf_a, rendered_event)
        self.assertNotIn(self.cpf_b, rendered_event)
        self.assertNotIn(new_hash, rendered_event)

    def test_update_sql_direto_tambem_gera_trilha_de_credencial(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE users SET senha_hash=?, credential_version=credential_version+1 "
                "WHERE user_id=?",
                (self._hash_fixture("9"), self.user_a),
            )
            connection.commit()
        finally:
            connection.close()
        event = self._sql(
            "SELECT actor_user_id, account_user_id, credential_version_before, "
            "credential_version_after FROM credential_audit_events"
        )[0]
        self.assertEqual(tuple(event), (self.user_a, self.user_a, 1, 2))

    def test_reset_administrativo_sempre_bloqueado_sem_alteracao(self):
        for administrator_id in (self.user_a, 999999):
            with self.subTest(administrator_id=administrator_id):
                before_a = persistencia.consultar_usuario_por_id(self.database, self.user_a)
                before_b = persistencia.consultar_usuario_por_id(self.database, self.user_b)
                before_balance = persistencia.consultar_conta(self.database, self.user_b)["balance_cents"]
                before_ledger = self._sql(
                    "SELECT transaction_id, delta_cents FROM financial_transactions WHERE user_id=?",
                    (self.user_b,),
                )
                with self.assertRaises(persistencia.AutorizacaoAdministrativaIndisponivel):
                    persistencia.redefinir_hash_senha_administrativamente(
                        self.database, administrator_id, self.user_b,
                        self._hash_fixture("f"), 1, "REQ-20261121",
                    )
                self.assertEqual(
                    persistencia.consultar_usuario_por_id(self.database, self.user_a), before_a
                )
                self.assertEqual(
                    persistencia.consultar_usuario_por_id(self.database, self.user_b), before_b
                )
                self.assertEqual(
                    persistencia.consultar_conta(self.database, self.user_b)["balance_cents"],
                    before_balance,
                )
                self.assertEqual(self._sql(
                    "SELECT transaction_id, delta_cents FROM financial_transactions WHERE user_id=?",
                    (self.user_b,),
                ), before_ledger)

        with persistencia.abrir_conexao(self.database) as connection:
            connection.execute("UPDATE users SET ativo=0 WHERE user_id=?", (self.user_a,))
        with self.assertRaises(persistencia.AutorizacaoAdministrativaIndisponivel):
            persistencia.redefinir_hash_senha_administrativamente(
                self.database, self.user_a, self.user_b, self._hash_fixture("f"), 1, "REQ-20261121"
            )
        self.assertEqual(self._sql("SELECT COUNT(*) FROM credential_audit_events")[0][0], 0)

    def test_falha_na_auditoria_reverte_hash_e_versao(self):
        old = persistencia.consultar_usuario_por_id(self.database, self.user_b)
        with persistencia.abrir_conexao(self.database) as connection:
            connection.execute(
                "CREATE TRIGGER force_credential_audit_failure "
                "BEFORE INSERT ON credential_audit_events "
                "BEGIN SELECT RAISE(ABORT, 'forced audit constraint failure'); END"
            )
        with self.assertRaises(persistencia.ErroPersistencia):
            persistencia.atualizar_hash_senha(
                self.database, self.user_b, self._hash_fixture("f"), 1
            )
        current = persistencia.consultar_usuario_por_id(self.database, self.user_b)
        self.assertEqual(current["senha_hash"], old["senha_hash"])
        self.assertEqual(current["credential_version"], 1)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM credential_audit_events")[0][0], 0)

    def test_auditoria_e_append_only_por_sql_direto(self):
        self.assertEqual(persistencia.atualizar_hash_senha(
            self.database, self.user_b, self._hash_fixture("f"), 1
        ), 2)
        event_id = self._sql("SELECT event_id FROM credential_audit_events")[0][0]
        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE credential_audit_events SET protocol='REQ-999' WHERE event_id=?",
                    (event_id,),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM credential_audit_events WHERE event_id=?", (event_id,))
            connection.rollback()
        finally:
            connection.close()

    def test_reset_nao_altera_saldo_ou_historico_financeiro(self):
        persistencia.registrar_deposito(
            self.database, self.user_b, "3.25", "credential-audit-finance-fixture-key"
        )
        before = persistencia.consultar_conta(self.database, self.user_b)
        ledger_before = self._sql(
            "SELECT transaction_id, operation_type, delta_cents FROM financial_transactions "
            "WHERE user_id=? ORDER BY transaction_id",
            (self.user_b,),
        )
        persistencia.atualizar_hash_senha(self.database, self.user_b, self._hash_fixture("f"), 1)
        after = persistencia.consultar_conta(self.database, self.user_b)
        ledger_after = self._sql(
            "SELECT transaction_id, operation_type, delta_cents FROM financial_transactions "
            "WHERE user_id=? ORDER BY transaction_id",
            (self.user_b,),
        )
        self.assertEqual(after["balance_cents"], before["balance_cents"])
        self.assertEqual(ledger_after, ledger_before)
        self.assertEqual(
            persistencia.consultar_usuario_por_id(self.database, self.user_a)["credential_version"],
            1,
        )

    def test_conversao_monetaria_exata_e_limites(self):
        for texto, centavos in (
            ("0,01", 1),
            ("0,10", 10),
            ("0,20", 20),
            ("0,30", 30),
            ("10,99", 1099),
            ("1000000000,00", 100_000_000_000),
        ):
            with self.subTest(texto=texto):
                self.assertEqual(persistencia.converter_para_centavos(texto), centavos)
        self.assertEqual(
            persistencia.converter_para_centavos(Decimal("0.30")), 30
        )
        self.assertEqual(
            persistencia.converter_valor_legado_para_centavos(Decimal("0.3")), 30
        )

    def test_conversao_rejeita_subcentavos_float_infinito_e_limite_excedido(self):
        invalidos = (
            "10,999",
            Decimal("1.230"),
            0.1,
            "NaN",
            "Infinity",
            "-0,01",
            "0,00",
            "1000000000,01",
            "1_000,00",
        )
        for valor in invalidos:
            with self.subTest(valor=type(valor).__name__):
                with self.assertRaises(persistencia.ErroPersistencia):
                    persistencia.converter_para_centavos(valor)
        with self.assertRaises(persistencia.LimiteFinanceiroExcedido):
            persistencia.converter_para_centavos("1000000000,01")

    def test_conversao_de_legado_nao_arredonda(self):
        self.assertEqual(persistencia.converter_valor_legado_para_centavos(Decimal("10.99")), 1099)
        for valor in (10.99, 10.999, float("nan"), float("inf"), Decimal("-1.0"), "10"):
            with self.subTest(tipo=type(valor).__name__):
                with self.assertRaises(persistencia.ErroPersistencia):
                    persistencia.converter_valor_legado_para_centavos(valor)

    def test_restricoes_impedem_saldo_fora_do_limite_e_referencia_orfa(self):
        connection = persistencia.abrir_conexao(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE accounts SET balance_cents = ? WHERE user_id = ?",
                    (100_000_000_001, self.user_a),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO financial_transactions"
                    "(user_id, operation_type, delta_cents, occurred_at)"
                    " VALUES (?, 'deposit', 10, '2026-01-01T00:00:00+00:00')",
                    (987654,),
                )
        finally:
            connection.close()
        self.assertEqual(persistencia.consultar_conta(self.database, self.user_a)["balance_cents"], 0)

    def test_razao_e_triggers_mantem_detalhe_e_saldo_consistentes(self):
        result = persistencia.registrar_deposito(
            self.database, self.user_a, "2,50", "integrity-deposit-token-001"
        )
        self.assertEqual(result.balance_cents, 250)
        self.assertEqual(self._sql("SELECT amount_cents FROM deposits")[0][0], 250)

        connection = persistencia.abrir_conexao(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE accounts SET balance_cents = 999 WHERE user_id = ?",
                    (self.user_a,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO financial_transactions"
                    "(user_id, operation_type, delta_cents, occurred_at, amount_cents)"
                    " VALUES (?, 'deposit', 100, '2026-01-01T00:00:00Z', 99)",
                    (self.user_a,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO financial_transactions"
                    "(user_id, operation_type, delta_cents, occurred_at)"
                    " VALUES (?, 'deposit', 100, '2026-01-01T00:00:00Z')",
                    (self.user_a,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO financial_transactions"
                    "(user_id, operation_type, delta_cents, occurred_at)"
                    " VALUES (?, 'bet_win', 10, '2026-01-01T00:00:00Z')",
                    (self.user_a,),
                )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO deposits(transaction_id, amount_cents, occurred_at)"
                    " VALUES (1, 1, '2026-01-01T00:00:00Z')"
                )
        finally:
            connection.close()
        self.assertEqual(persistencia.consultar_conta(self.database, self.user_a)["balance_cents"], 250)

    def test_sql_rejeita_centavos_e_numeros_de_jogo_fracionarios(self):
        persistencia.registrar_deposito(
            self.database, self.user_a, "1,00", "fractional-fields-seed-token-1"
        )
        invalid_bets = (
            ("bet_win", 15, 1.5, 4, 4, "win"),
            ("bet_win", 10, 1, 4.5, 4.5, "win"),
            ("bet_loss", -1, 1, 4, 4.5, "loss"),
        )
        for number, (operation, delta, wager, chosen, drawn, outcome) in enumerate(invalid_bets):
            with self.subTest(number=number):
                connection = persistencia.abrir_conexao(self.database)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO idempotency_keys"
                        "(key_hash,user_id,operation_type,state,created_at)"
                        " VALUES (?,?,'bet','processing','2026-01-01T00:00:00Z')",
                        (f"{number + 1:064x}", self.user_a),
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO financial_transactions"
                            "(user_id,operation_type,delta_cents,occurred_at,wager_cents,"
                            "chosen_number,drawn_number,outcome) VALUES (?,?,?,?,?,?,?,?)",
                            (
                                self.user_a, operation, delta, "2026-01-01T00:00:00Z",
                                wager, chosen, drawn, outcome,
                            ),
                        )
                    connection.rollback()
                finally:
                    connection.close()

    def test_trigger_sql_exige_cobertura_de_derrota_e_espaco_para_premio(self):
        def tentativa_aposta(user_id, key_hash, operation, delta):
            connection = persistencia.abrir_conexao(self.database)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO idempotency_keys"
                    "(key_hash,user_id,operation_type,state,created_at)"
                    " VALUES (?,?,'bet','processing','2026-01-01T00:00:00Z')",
                    (key_hash, user_id),
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO financial_transactions"
                        "(user_id,operation_type,delta_cents,occurred_at,wager_cents,"
                        "chosen_number,drawn_number,outcome) VALUES (?,?,?,?,?,?,?,?)",
                        (user_id, operation, delta, "2026-01-01T00:00:00Z", 1, 2, 2 if operation == "bet_win" else 3,
                         "win" if operation == "bet_win" else "loss"),
                    )
                connection.rollback()
            finally:
                connection.close()

        tentativa_aposta(self.user_a, "d" * 64, "bet_win", 10)

        persistencia.registrar_deposito(
            self.database, self.user_a, "999999999,95", "precheck-space-seed-token-1"
        )
        tentativa_aposta(self.user_a, "e" * 64, "bet_loss", -1)

    def test_identidade_da_conta_nao_pode_ser_trocada_por_sql(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with persistencia.transacao_escrita(self.database) as connection:
                connection.execute(
                    "UPDATE accounts SET user_id = ? WHERE user_id = ?",
                    (self.user_b, self.user_a),
                )
        self.assertEqual(
            persistencia.consultar_conta(self.database, self.user_a)["user_id"], self.user_a
        )
        self.assertEqual(
            persistencia.consultar_conta(self.database, self.user_b)["user_id"], self.user_b
        )

    def test_chave_concluida_nao_pode_ser_apagada_ou_alterada_por_sql(self):
        result = persistencia.registrar_deposito(
            self.database, self.user_a, "1,00", "immutable-key-token-00001"
        )
        connection = persistencia.abrir_conexao(self.database)
        try:
            key_hash = connection.execute(
                "SELECT key_hash FROM idempotency_keys WHERE transaction_id = ?",
                (result.transaction_id,),
            ).fetchone()[0]
            statements = (
                ("DELETE FROM idempotency_keys WHERE key_hash = ?", (key_hash,)),
                ("UPDATE idempotency_keys SET key_hash = ? WHERE key_hash = ?", ("a" * 64, key_hash)),
                ("UPDATE idempotency_keys SET user_id = ? WHERE key_hash = ?", (self.user_b, key_hash)),
                ("UPDATE idempotency_keys SET operation_type = 'bet' WHERE key_hash = ?", (key_hash,)),
                ("UPDATE idempotency_keys SET transaction_id = ? WHERE key_hash = ?", (result.transaction_id + 100, key_hash)),
                ("UPDATE idempotency_keys SET result_json = '{}' WHERE key_hash = ?", (key_hash,)),
            )
            for statement, params in statements:
                with self.subTest(statement=statement.split()[0:3]):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, params)
            saved = connection.execute(
                "SELECT user_id, operation_type, state, transaction_id "
                "FROM idempotency_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            self.assertEqual(tuple(saved), (self.user_a, "deposit", "completed", result.transaction_id))
        finally:
            connection.close()

    def test_chave_so_pode_concluir_com_movimentacao_do_mesmo_usuario_e_tipo(self):
        connection = persistencia.abrir_conexao(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            key_a = "a" * 64
            key_b = "b" * 64
            key_bet = "c" * 64
            connection.execute(
                "INSERT INTO idempotency_keys"
                "(key_hash,user_id,operation_type,state,created_at)"
                " VALUES (?,?,'deposit','processing','2026-01-01T00:00:00Z')",
                (key_a, self.user_a),
            )
            connection.execute(
                "INSERT INTO idempotency_keys"
                "(key_hash,user_id,operation_type,state,created_at)"
                " VALUES (?,?,'deposit','processing','2026-01-01T00:00:00Z')",
                (key_b, self.user_b),
            )
            connection.execute(
                "INSERT INTO idempotency_keys"
                "(key_hash,user_id,operation_type,state,created_at)"
                " VALUES (?,?,'bet','processing','2026-01-01T00:00:00Z')",
                (key_bet, self.user_a),
            )
            tx_a = connection.execute(
                "INSERT INTO financial_transactions"
                "(user_id,operation_type,delta_cents,occurred_at,amount_cents)"
                " VALUES (?,'deposit',100,'2026-01-01T00:00:00Z',100)",
                (self.user_a,),
            ).lastrowid
            tx_b = connection.execute(
                "INSERT INTO financial_transactions"
                "(user_id,operation_type,delta_cents,occurred_at,amount_cents)"
                " VALUES (?,'deposit',100,'2026-01-01T00:00:00Z',100)",
                (self.user_b,),
            ).lastrowid
            tx_bet = connection.execute(
                "INSERT INTO financial_transactions"
                "(user_id,operation_type,delta_cents,occurred_at,wager_cents,"
                "chosen_number,drawn_number,outcome)"
                " VALUES (?,'bet_win',10,'2026-01-01T00:00:00Z',1,2,2,'win')",
                (self.user_a,),
            ).lastrowid
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE idempotency_keys SET state='completed',transaction_id=?,result_json='{}' "
                    "WHERE key_hash=?", (tx_a, key_a)
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE idempotency_keys SET state='completed',transaction_id=?,result_json='{}' "
                    "WHERE key_hash=?", (tx_b, key_a)
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE idempotency_keys SET state='completed',transaction_id=?,result_json='{}' "
                    "WHERE key_hash=?", (tx_bet, key_a)
                )
            connection.rollback()
        finally:
            connection.close()

    def test_historico_legado_e_append_only_mas_aceita_novas_importacoes(self):
        connection = persistencia.abrir_conexao(self.database)
        try:
            bundle_hash = "f" * 64
            connection.execute(
                "INSERT INTO migration_batches"
                "(bundle_hash,imported_at,account_count,deposit_history_count,bet_history_count)"
                " VALUES (?,'2026-01-01T00:00:00Z',2,1,1)", (bundle_hash,)
            )
            connection.execute(
                "INSERT INTO legacy_history"
                "(bundle_hash,source_file,source_row,history_type,user_id,original_data_json,record_hash)"
                " VALUES (?,'depositos.json',100,'deposit',?,'{}',?)",
                (bundle_hash, self.user_a, "1" * 64),
            )
            legacy_id = connection.execute(
                "SELECT legacy_id FROM legacy_history WHERE source_row=100"
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE legacy_history SET source_row=101 WHERE legacy_id=?", (legacy_id,)
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM legacy_history WHERE legacy_id=?", (legacy_id,)
                )
            connection.execute(
                "INSERT INTO legacy_history"
                "(bundle_hash,source_file,source_row,history_type,user_id,original_data_json,record_hash)"
                " VALUES (?,'apostas.json',101,'bet',?,'{}',?)",
                (bundle_hash, self.user_a, "2" * 64),
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_history").fetchone()[0], 2)
        finally:
            connection.close()

    def test_deposito_atualiza_saldo_historico_e_repeticao_retorna_resultado_original(self):
        chave = "deposit-request-token-0001"
        first = persistencia.registrar_deposito(
            self.database, self.user_a, "0,30", chave,
            ocorrido_em="2026-01-02T03:04:05+00:00",
        )
        repeated = persistencia.registrar_deposito(
            self.database, self.user_a, "99,00", chave,
            ocorrido_em="2026-01-03T03:04:05+00:00",
        )
        self.assertEqual(first.balance_cents, 30)
        self.assertEqual(repeated.balance_cents, 30)
        self.assertEqual(repeated.transaction_id, first.transaction_id)
        self.assertTrue(repeated.replayed)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM deposits")[0][0], 1)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM financial_transactions WHERE operation_type='deposit'")[0][0], 1)
        self.assertEqual(persistencia.validar_chave_idempotencia(
            self.database, self.user_a, "deposit", chave
        ).transaction_id, first.transaction_id)

    def test_vitoria_credita_dez_vezes_sem_debito_previo(self):
        persistencia.registrar_deposito(self.database, self.user_a, "10,00", "deposit-seed-token-0001")
        draws = []

        def controlled_draw():
            draws.append(7)
            return 7

        result = persistencia.registrar_aposta(
            self.database, self.user_a, "5,00", 7, controlled_draw,
            "bet-win-token-00000001",
            ocorrido_em="2026-01-04T03:04:05+00:00",
        )
        self.assertEqual(draws, [7])
        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.delta_cents, 5000)
        self.assertEqual(result.balance_cents, 6000)
        bet = self._sql("SELECT wager_cents, chosen_number, drawn_number, outcome, balance_delta_cents FROM bets")
        self.assertEqual(tuple(bet[0]), (500, 7, 7, "win", 5000))

    def test_derrota_debita_somente_a_aposta(self):
        persistencia.registrar_deposito(self.database, self.user_a, "10,00", "deposit-seed-token-0002")
        result = persistencia.registrar_aposta(
            self.database, self.user_a, "7,00", 2, lambda: 3,
            "bet-loss-token-00000001",
        )
        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.delta_cents, -700)
        self.assertEqual(result.balance_cents, 300)

    def test_saldo_insuficiente_e_numero_invalido_nao_chamam_sorteador(self):
        calls = []
        draw = lambda: calls.append(True) or 4
        with self.assertRaises(persistencia.SaldoInsuficiente):
            persistencia.registrar_aposta(
                self.database, self.user_a, "0,01", 4, draw,
                "bet-low-balance-token-01",
            )
        with self.assertRaises(persistencia.ErroValidacao):
            persistencia.registrar_aposta(
                self.database, self.user_a, "0,01", 11, draw,
                "bet-invalid-number-token-01",
            )
        self.assertEqual(calls, [])
        self.assertEqual(self._sql("SELECT COUNT(*) FROM bets")[0][0], 0)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM idempotency_keys")[0][0], 0)

    def test_limite_de_saldo_previsto_na_vitoria_e_chegado_antes_do_sorteio(self):
        persistencia.registrar_deposito(
            self.database, self.user_a, "999999999,95", "near-limit-balance-token-01"
        )
        calls = []
        with self.assertRaises(persistencia.LimiteFinanceiroExcedido):
            persistencia.registrar_aposta(
                self.database, self.user_a, "0,01", 4,
                lambda: calls.append(True) or 4,
                "bet-limit-token-0000001",
            )
        self.assertEqual(calls, [])

    def test_falha_no_historico_reverte_saldo_transacao_e_idempotencia(self):
        original_balance = persistencia.consultar_conta(self.database, self.user_a)["balance_cents"]
        with patch.object(
            persistencia,
            "_finalizar_idempotencia",
            side_effect=RuntimeError("falha simulada após gravações financeiras"),
        ):
            with self.assertRaises(RuntimeError):
                persistencia.registrar_deposito(
                    self.database, self.user_a, "1,00", "rollback-deposit-token-01"
                )
        self.assertEqual(persistencia.consultar_conta(self.database, self.user_a)["balance_cents"], original_balance)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM deposits")[0][0], 0)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM financial_transactions WHERE operation_type='deposit'")[0][0], 0)
        self.assertIsNone(persistencia.consultar_idempotencia(
            self.database, self.user_a, "deposit", "rollback-deposit-token-01"
        ))

    def test_falha_apos_sorteio_controlado_reverte_toda_aposta(self):
        persistencia.registrar_deposito(self.database, self.user_a, "10,00", "bet-rollback-seed-token-01")
        with patch.object(
            persistencia,
            "_finalizar_idempotencia",
            side_effect=RuntimeError("falha simulada"),
        ):
            with self.assertRaises(RuntimeError):
                persistencia.registrar_aposta(
                    self.database, self.user_a, "1,00", 6, lambda: 6,
                    "bet-rollback-token-00001",
                )
        self.assertEqual(persistencia.consultar_conta(self.database, self.user_a)["balance_cents"], 1000)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM bets")[0][0], 0)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM financial_transactions WHERE operation_type LIKE 'bet_%'")[0][0], 0)
        self.assertIsNone(persistencia.consultar_idempotencia(
            self.database, self.user_a, "bet", "bet-rollback-token-00001"
        ))

    def test_idempotencia_concorrente_processa_deposito_uma_vez(self):
        key = "parallel-deposit-token-0001"

        def deposit():
            return persistencia.registrar_deposito(self.database, self.user_a, "0,10", key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: deposit(), range(6)))
        self.assertEqual(persistencia.consultar_conta(self.database, self.user_a)["balance_cents"], 10)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM deposits")[0][0], 1)
        self.assertEqual(len({item.transaction_id for item in results}), 1)
        self.assertEqual(sum(item.replayed for item in results), 5)
        hashes = self._sql("SELECT key_hash FROM idempotency_keys")
        self.assertEqual(len(hashes), 1)
        self.assertNotEqual(hashes[0][0], key)
        self.assertRegex(persistencia.gerar_chave_idempotencia(), r"^[A-Za-z0-9_-]{43}$")

    def test_finalizacao_de_idempotencia_exige_uma_linha(self):
        class CursorFake:
            def __init__(self, rowcount):
                self.rowcount = rowcount

        class ConnectionFake:
            def __init__(self, rowcount):
                self.rowcount = rowcount

            def execute(self, *_args):
                return CursorFake(self.rowcount)

        result = persistencia.ResultadoOperacao(
            transaction_id=1,
            user_id=self.user_a,
            operation_type="deposit",
            delta_cents=100,
            balance_cents=100,
            amount_cents=100,
        )
        with self.assertRaises(persistencia.ErroPersistencia):
            persistencia._finalizar_idempotencia(ConnectionFake(0), "a" * 64, result)
        with self.assertRaises(persistencia.ErroPersistencia):
            persistencia._finalizar_idempotencia(ConnectionFake(2), "a" * 64, result)

    def test_operacoes_simultaneas_independentes_nao_perdem_atualizacoes(self):
        keys = [f"parallel-independent-token-{n:04d}" for n in range(8)]

        def deposit(key):
            return persistencia.registrar_deposito(self.database, self.user_a, "0,01", key)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(deposit, keys))
        self.assertEqual(persistencia.consultar_conta(self.database, self.user_a)["balance_cents"], 8)
        self.assertEqual(self._sql("SELECT COUNT(*) FROM deposits")[0][0], 8)

    def test_historico_retorna_somente_a_conta_solicitada(self):
        persistencia.registrar_deposito(self.database, self.user_a, "1,00", "history-user-a-token-1")
        persistencia.registrar_deposito(self.database, self.user_b, "2,00", "history-user-b-token-1")
        history_a = persistencia.consultar_historico(self.database, self.user_a)
        self.assertEqual(len(history_a), 2)  # transação inicial e depósito
        deposito = next(row for row in history_a if row["operation_type"] == "deposit")
        self.assertEqual(deposito["amount_cents"], 100)
        self.assertEqual({row["operation_type"] for row in history_a}, {"opening_balance", "deposit"})

    def test_backup_e_restauracao_temporarios_preservam_dados(self):
        persistencia.registrar_deposito(self.database, self.user_a, "3,25", "backup-token-000000001")
        backup = self.root / "backup.sqlite3"
        restored = self.root / "restored.sqlite3"
        persistencia.backup_banco(self.database, backup)
        persistencia.restaurar_backup(backup, restored)
        self.assertEqual(persistencia.verificar_integridade(backup), "ok")
        self.assertEqual(persistencia.verificar_integridade(restored), "ok")
        account = persistencia.consultar_conta(restored, self.user_a)
        self.assertEqual(account["balance_cents"], 325)
        with self.assertRaises(FileExistsError):
            persistencia.backup_banco(self.database, backup)


if __name__ == "__main__":
    unittest.main()
