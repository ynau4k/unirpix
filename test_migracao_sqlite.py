import io
import json
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import migrar_financas_sqlite as migracao
import persistencia_sqlite as persistencia


class TestMigracaoSQLite(unittest.TestCase):
    cpf_a = "11144477735"
    cpf_b = "22457787823"
    nome_a = "Conta Ficticia Alfa"
    nome_b = "Conta Ficticia Beta"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.fontes = self.root / "fontes"
        self.fontes.mkdir()
        self._escrever_fontes_validas()
        self.banco = self.root / "migracao.sqlite3"

    def tearDown(self):
        self.tempdir.cleanup()

    def _gravar(self, nome, dados):
        (self.fontes / nome).write_text(
            json.dumps(dados, ensure_ascii=False, allow_nan=True), encoding="utf-8"
        )

    def _gravar_texto(self, nome, texto):
        (self.fontes / nome).write_text(texto, encoding="utf-8")

    def _fontes_dict(self):
        return {nome: self.fontes / nome for nome in migracao.ARQUIVOS_ORIGEM}

    def _escrever_fontes_validas(self):
        self._gravar(
            "cadastro.json",
            [
                {
                    "cpf": self.cpf_a,
                    "nome": self.nome_a,
                    "saldo": 20.30,
                    "senha_hash": f"scrypt:32768:8:1$fixtureSalt000001${'a' * 128}",
                },
                {"cpf": self.cpf_b, "nome": self.nome_b, "saldo": 0},
            ],
        )
        self._gravar(
            "depositos.json",
            [{"cpf": self.cpf_a, "valor": 4.15}],
        )
        self._gravar(
            "apostas.json",
            [{
                "cpf": self.cpf_a,
                "valor_apostado": 1.25,
                "numero_escolhido": 8,
                "numero_sorteado": 8,
                "resultado": "Vitória!",
                "valor_resultado": 12.5,
            }],
        )

    def _count(self, table):
        connection = persistencia.abrir_conexao(self.banco)
        try:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            connection.close()

    def test_dry_run_valida_copias_sem_criar_banco_e_sem_expor_identidade(self):
        report, _rows, _metadata = migracao.analisar_jsons(self._fontes_dict())
        self.assertTrue(report.elegivel)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.contagens_origem, {
            "cadastro.json": 2,
            "depositos.json": 1,
            "apostas.json": 1,
        })
        self.assertEqual(report.datas_ausentes, {
            "depositos.json": 1,
            "apostas.json": 1,
        })
        serialized = json.dumps(report.como_dict(), ensure_ascii=False)
        self.assertNotIn(self.cpf_a, serialized)
        self.assertNotIn(self.nome_a, serialized)
        self.assertFalse(self.banco.exists())

    def test_parser_preserva_decimal_de_alta_precisao_e_notacao_exponencial(self):
        source = self.root / "decimais.json"
        source.write_text(
            '[{"preciso":12345678901234567890.123456789,"expoente":1.25e1}]',
            encoding="utf-8",
        )
        rows = migracao._carregar_json(source)
        self.assertIsInstance(rows[0]["preciso"], Decimal)
        self.assertEqual(rows[0]["preciso"], Decimal("12345678901234567890.123456789"))
        self.assertEqual(rows[0]["expoente"], Decimal("1.25e1"))
        self.assertEqual(migracao._money_category(rows[0]["expoente"], signed=False, allow_zero=False), ("exact", 1250))
        self.assertEqual(
            migracao._money_category(Decimal("1.230e0"), signed=False, allow_zero=False),
            ("review", None),
        )

    def test_valor_acima_do_limite_e_inseguro_e_analise_continua(self):
        self._gravar_texto(
            "depositos.json",
            '[{"cpf":"11144477735","valor":1000000000.01},'
            '{"cpf":"11144477735","valor":2.00},'
            '{"cpf":"11144477735","valor":-1}]',
        )

        report, registros, metadados = migracao.analisar_jsons(self._fontes_dict())

        self.assertFalse(report.elegivel)
        self.assertEqual(report.contagens_origem["depositos.json"], 3)
        self.assertEqual(report.problemas["valor_fora_do_limite_seguro"], 1)
        self.assertEqual(report.problemas["valor_monetario_invalido"], 1)
        self.assertEqual(report.estatisticas_monetarias["unsafe"], 1)
        self.assertEqual(report.estatisticas_monetarias["exact"], 5)
        self.assertEqual(registros["depositos.json"][1]["valor"], Decimal("2.00"))
        self.assertEqual(
            metadados["depositos.json"][1]["centavos_legados"]["valor"], 200
        )

    def test_main_emite_diagnostico_sem_traceback_para_valor_acima_do_limite(self):
        self._gravar_texto(
            "depositos.json",
            '[{"cpf":"11144477735","valor":1000000000.01},'
            '{"cpf":"11144477735","valor":3.00}]',
        )
        args = SimpleNamespace(
            cadastro=str(self.fontes / "cadastro.json"),
            depositos=str(self.fontes / "depositos.json"),
            apostas=str(self.fontes / "apostas.json"),
            banco=None,
            aplicar=False,
        )
        output = io.StringIO()
        with patch.object(migracao, "_argumentos", return_value=args), patch(
            "sys.stdout", output
        ):
            exit_code = migracao.main()

        self.assertEqual(exit_code, 1)
        rendered = output.getvalue()
        self.assertNotIn("Traceback", rendered)
        report = json.loads(rendered)
        self.assertEqual(report["problemas"]["valor_fora_do_limite_seguro"], 1)
        self.assertEqual(report["estatisticas_monetarias"]["unsafe"], 1)
        self.assertEqual(report["estatisticas_monetarias"]["exact"], 5)

    def test_serializacao_mantem_decimais_como_numeros_json(self):
        value = {
            "decimal": Decimal("1.23000000000000000001"),
            "exponencial": Decimal("1.25E+2"),
        }
        serialized = migracao._canonical_json(value)
        self.assertIn('"decimal":1.23000000000000000001', serialized)
        self.assertIn('"exponencial":125', serialized)
        self.assertNotIn('"1.23000000000000000001"', serialized)
        restored = json.loads(serialized, parse_float=Decimal)
        self.assertEqual(restored, value)

    def test_serializacao_decimal_mantem_expoente_json_sem_aspas(self):
        serialized = migracao._canonical_json({"pequeno": Decimal("1.25E-10")})
        self.assertEqual(serialized, '{"pequeno":1.25E-10}')
        self.assertEqual(json.loads(serialized, parse_float=Decimal)["pequeno"], Decimal("1.25E-10"))

    def test_fingerprint_decimal_e_deterministico(self):
        self._gravar_texto(
            "depositos.json",
            f'[{{"cpf":"{self.cpf_a}","valor":4.15,"campo_extra":1.2300e-4}}]',
        )
        _report_a, rows_a, _meta_a = migracao.analisar_jsons(self._fontes_dict())
        _report_b, rows_b, _meta_b = migracao.analisar_jsons(self._fontes_dict())
        self.assertEqual(migracao._fingerprint(rows_a), migracao._fingerprint(rows_b))
        self.assertEqual(rows_a["depositos.json"][0]["campo_extra"], Decimal("1.2300e-4"))

    def test_fingerprint_canoniza_ordem_de_chaves_e_decimais_equivalentes(self):
        first = {
            "cadastro.json": [{"nome": "Teste", "saldo": Decimal("12.50")}],
            "depositos.json": [],
            "apostas.json": [],
        }
        second = {
            "apostas.json": [],
            "depositos.json": [],
            "cadastro.json": [{"saldo": Decimal("1.250e1"), "nome": "Teste"}],
        }
        self.assertEqual(migracao._fingerprint(first), migracao._fingerprint(second))

    def test_importacao_preserva_decimal_legado_sem_converter_para_float(self):
        self._gravar_texto(
            "depositos.json",
            f'[{{"cpf":"{self.cpf_a}","valor":4.15,'
            '"campo_extra":12345678901234567890.123456789,"data_hora":null}]',
        )
        report = migracao.importar_jsons_temporarios(
            self._fontes_dict(), self.banco, dry_run=False
        )
        self.assertTrue(report.elegivel)
        connection = persistencia.abrir_conexao(self.banco)
        try:
            raw = connection.execute(
                "SELECT original_data_json FROM legacy_history WHERE source_file='depositos.json'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn('"campo_extra":12345678901234567890.123456789', raw)
        restored = json.loads(raw, parse_float=Decimal)
        self.assertEqual(
            restored["campo_extra"], Decimal("12345678901234567890.123456789")
        )

    def test_falha_intermediaria_reverte_importacao_e_permite_retentar(self):
        original = migracao._timestamp_original
        calls = 0

        def fail_during_second_history(row):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("falha simulada durante migração")
            return original(row)

        with patch.object(migracao, "_timestamp_original", side_effect=fail_during_second_history):
            with self.assertRaises(ValueError):
                migracao.importar_jsons_temporarios(
                    self._fontes_dict(), self.banco, dry_run=False
                )

        connection = persistencia.abrir_conexao(self.banco)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM financial_transactions").fetchone()[0],
                0,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_history").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM migration_batches").fetchone()[0], 0)
        finally:
            connection.close()

        retry = migracao.importar_jsons_temporarios(
            self._fontes_dict(), self.banco, dry_run=False
        )
        self.assertTrue(retry.elegivel)
        self.assertEqual(retry.importado["users"], 2)

    def test_importacao_preserva_saldos_e_historicos_sem_reaplicar_movimentos(self):
        original_bytes = {
            name: (self.fontes / name).read_bytes()
            for name in migracao.ARQUIVOS_ORIGEM
        }

        report = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)

        self.assertTrue(report.elegivel)
        self.assertFalse(report.dry_run)
        self.assertEqual(report.importado, {
            "users": 2,
            "financial_transactions": 2,
            "legacy_history": 2,
        })
        self.assertEqual(self._count("users"), 2)
        self.assertEqual(self._count("accounts"), 2)
        self.assertEqual(self._count("financial_transactions"), 2)
        self.assertEqual(self._count("legacy_history"), 2)
        # Os históricos legados são arquivados, não reaplicados ao saldo.
        self.assertEqual(self._count("deposits"), 0)
        self.assertEqual(self._count("bets"), 0)

        account_a = persistencia.consultar_conta_por_cpf(self.banco, self.cpf_a)
        account_b = persistencia.consultar_conta_por_cpf(self.banco, self.cpf_b)
        self.assertEqual(account_a["balance_cents"], 2030)
        self.assertEqual(account_b["balance_cents"], 0)
        self.assertEqual(
            account_a["senha_hash"], f"scrypt:32768:8:1$fixtureSalt000001${'a' * 128}"
        )
        self.assertEqual(self._count("migration_batches"), 1)

        connection = persistencia.abrir_conexao(self.banco)
        try:
            legacy = connection.execute(
                "SELECT source_file, source_row, history_type, original_data_json, original_datetime "
                "FROM legacy_history ORDER BY source_file, source_row"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [(row["source_file"], row["source_row"], row["history_type"], row["original_datetime"]) for row in legacy],
            [("apostas.json", 0, "bet", None), ("depositos.json", 0, "deposit", None)],
        )
        bet_record = next(json.loads(row["original_data_json"]) for row in legacy if row["history_type"] == "bet")
        self.assertEqual(bet_record["valor_apostado"], 1.25)
        self.assertEqual(bet_record["valor_resultado"], 12.5)
        self.assertEqual(
            original_bytes,
            {name: (self.fontes / name).read_bytes() for name in migracao.ARQUIVOS_ORIGEM},
        )

    def test_reimportacao_identica_e_idempotente(self):
        first = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)
        second = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)
        self.assertFalse(first.ja_importado)
        self.assertTrue(second.ja_importado)
        self.assertEqual(self._count("users"), 2)
        self.assertEqual(self._count("legacy_history"), 2)
        self.assertEqual(self._count("financial_transactions"), 2)

    def test_novo_lote_acrescenta_historico_sem_resetar_saldos_ou_duplicar(self):
        first = migracao.importar_jsons_temporarios(
            self._fontes_dict(), self.banco, dry_run=False
        )
        self.assertTrue(first.elegivel)
        old_balance = persistencia.consultar_conta_por_cpf(self.banco, self.cpf_a)["balance_cents"]

        users = json.loads((self.fontes / "cadastro.json").read_text(encoding="utf-8"))
        users[0]["saldo"] = 999.00
        self._gravar("cadastro.json", users)
        deposits = json.loads((self.fontes / "depositos.json").read_text(encoding="utf-8"))
        deposits.append({
            "cpf": self.cpf_a,
            "valor": 2.00,
            "data_hora": "2026-03-04T05:06:07+00:00",
        })
        self._gravar("depositos.json", deposits)

        second = migracao.importar_jsons_temporarios(
            self._fontes_dict(), self.banco, dry_run=False
        )
        self.assertTrue(second.elegivel)
        self.assertFalse(second.ja_importado)
        self.assertEqual(second.importado, {
            "users": 0,
            "financial_transactions": 0,
            "legacy_history": 1,
        })
        self.assertEqual(
            persistencia.consultar_conta_por_cpf(self.banco, self.cpf_a)["balance_cents"],
            old_balance,
        )
        self.assertEqual(self._count("legacy_history"), 3)
        self.assertEqual(self._count("migration_batches"), 2)

        repeated = migracao.importar_jsons_temporarios(
            self._fontes_dict(), self.banco, dry_run=False
        )
        self.assertTrue(repeated.ja_importado)
        self.assertEqual(self._count("legacy_history"), 3)

    def test_cpf_duplicado_bloqueia_importacao_antes_de_criar_banco(self):
        rows = json.loads((self.fontes / "cadastro.json").read_text(encoding="utf-8"))
        rows[1]["cpf"] = self.cpf_a
        self._gravar("cadastro.json", rows)

        report = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)

        self.assertFalse(report.elegivel)
        self.assertGreater(report.problemas.get("cpf_duplicado_no_cadastro", 0), 0)
        self.assertFalse(self.banco.exists())

    def test_valor_subcentavo_bloqueia_importacao_sem_arredondar(self):
        rows = json.loads((self.fontes / "depositos.json").read_text(encoding="utf-8"))
        rows[0]["valor"] = 10.999
        self._gravar("depositos.json", rows)

        report = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)

        self.assertFalse(report.elegivel)
        self.assertEqual(report.estatisticas_monetarias.get("review"), 1)
        self.assertGreater(report.problemas.get("valor_com_mais_de_duas_casas", 0), 0)
        self.assertFalse(self.banco.exists())

    def test_valor_nan_e_referencia_orfa_bloqueiam_importacao(self):
        self._gravar("depositos.json", [{"cpf": self.cpf_b, "valor": float("nan")}])

        report = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)

        self.assertFalse(report.elegivel)
        self.assertGreater(report.problemas.get("json_ilegivel_ou_estrutura_invalida", 0), 0)
        self.assertFalse(self.banco.exists())

    def test_parser_rejeita_todas_as_constantes_json_nao_finitas(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                source = self.root / "nao_finito.json"
                source.write_text(f'[{token}]', encoding="utf-8")
                with self.assertRaises(ValueError):
                    migracao._carregar_json(source)

    def test_historico_exato_duplicado_e_bloqueado(self):
        rows = json.loads((self.fontes / "depositos.json").read_text(encoding="utf-8"))
        rows.append(dict(rows[0]))
        self._gravar("depositos.json", rows)

        report = migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)

        self.assertFalse(report.elegivel)
        self.assertEqual(report.problemas.get("registro_exato_duplicado"), 1)
        self.assertFalse(self.banco.exists())

    def test_destino_de_producao_e_recusado_sem_criacao(self):
        outside = Path(__file__).resolve().parent / "nao_criar_financeiro.sqlite3"
        report, _rows, _metadata = migracao.analisar_jsons(self._fontes_dict())
        self.assertTrue(report.elegivel)
        with self.assertRaises(migracao.ErroMigracao):
            migracao.importar_jsons_temporarios(
                self._fontes_dict(), outside, dry_run=False
            )
        self.assertFalse(outside.exists())

    def test_importacao_recusa_hardlink_antes_de_criar_o_banco(self):
        source = self.fontes / "depositos.json"
        with tempfile.TemporaryDirectory() as external_dir:
            external = Path(external_dir) / "fonte_original.json"
            external.write_bytes(source.read_bytes())
            source.unlink()
            os.link(external, source)

            with self.assertRaises(migracao.ErroMigracao):
                migracao.importar_jsons_temporarios(
                    self._fontes_dict(), self.banco, dry_run=False
                )

            self.assertFalse(self.banco.exists())
            self.assertGreater(source.stat().st_nlink, 1)

    def test_banco_temporario_nao_vazio_de_outra_importacao_e_preservado(self):
        persistencia.inicializar_banco(self.banco)
        user_id = persistencia.criar_usuario(
            self.banco,
            "39053344705",
            "Conta teste existente",
            f"scrypt:32768:8:1$fixtureSalt000001${'c' * 128}",
        )
        before = persistencia.consultar_conta(self.banco, user_id)

        with self.assertRaises(migracao.ErroMigracao):
            migracao.importar_jsons_temporarios(self._fontes_dict(), self.banco, dry_run=False)

        after = persistencia.consultar_conta(self.banco, user_id)
        self.assertEqual(after["balance_cents"], before["balance_cents"])
        self.assertEqual(self._count("users"), 1)


if __name__ == "__main__":
    unittest.main()
