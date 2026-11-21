import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import controle_saldo as modulo_saldo
import deposito
from controle_saldo import controle_saldo


class TestDeposito(unittest.TestCase):
    cpf = "11144477735"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.diretorio_dados_original = modulo_saldo.DIRETORIO_DADOS
        modulo_saldo.DIRETORIO_DADOS = Path(self.tempdir.name)
        self.diretorio_execucao_original = os.getcwd()
        self.diretorio_execucao = Path(self.tempdir.name) / "execucao"
        self.diretorio_execucao.mkdir()
        os.chdir(self.diretorio_execucao)
        with open(self.caminho("cadastro.json"), "w", encoding="utf-8") as arquivo:
            json.dump([{"nome": "Pessoa de Teste", "cpf": self.cpf, "saldo": 100.0}], arquivo)

    def tearDown(self):
        os.chdir(self.diretorio_execucao_original)
        modulo_saldo.DIRETORIO_DADOS = self.diretorio_dados_original
        self.tempdir.cleanup()

    def caminho(self, nome):
        return Path(self.tempdir.name) / nome

    def ler_depositos(self):
        with open(self.caminho("depositos.json"), "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def test_servico_deposito_preserva_saldo_e_esquema_do_historico(self):
        with patch("builtins.input", side_effect=AssertionError("input não esperado")):
            resultado = deposito.registrar_deposito(self.cpf, "12,50")

        self.assertEqual(resultado, "Depósito realizado com sucesso!")
        self.assertEqual(controle_saldo(self.cpf), 112.5)
        historico = self.ler_depositos()
        self.assertEqual(historico, [{"cpf": self.cpf, "valor": 12.5}])
        self.assertEqual(set(historico[0]), {"cpf", "valor"})

    def test_interface_de_terminal_continua_depositando(self):
        with patch("builtins.input", side_effect=[self.cpf, "2,50"]):
            resultado = deposito.depositar()

        self.assertEqual(resultado, "Depósito realizado com sucesso!")
        self.assertEqual(controle_saldo(self.cpf), 102.5)
        self.assertEqual(self.ler_depositos(), [{"cpf": self.cpf, "valor": 2.5}])

    def test_depositos_invalidos_nao_atualizam_saldo_nem_historico(self):
        for valor in (0, -1, "nan", "inf", "texto"):
            with self.subTest(valor=valor):
                resultado = deposito.registrar_deposito(self.cpf, valor)
                self.assertIn("positivo e finito", resultado)

        self.assertEqual(controle_saldo(self.cpf), 100.0)
        self.assertFalse(self.caminho("depositos.json").exists())

    def test_cpf_inexistente_nao_registra_deposito(self):
        resultado = deposito.registrar_deposito("99999999999", 10)

        self.assertEqual(resultado, "CPF não encontrado.")
        self.assertEqual(controle_saldo(self.cpf), 100.0)
        self.assertFalse(self.caminho("depositos.json").exists())

    def test_falha_ao_gravar_historico_reverte_o_credito(self):
        with patch("deposito.finalizar_json", side_effect=OSError("falha simulada")):
            resultado = deposito.registrar_deposito(self.cpf, 10)

        self.assertIn("saldo foi revertido", resultado)
        self.assertEqual(controle_saldo(self.cpf), 100.0)
        self.assertFalse(self.caminho("depositos.json").exists())
        self.assertFalse(any(nome.endswith(".tmp") for nome in os.listdir(self.tempdir.name)))

    def test_historico_invalido_impede_credito(self):
        with open(self.caminho("depositos.json"), "w", encoding="utf-8") as arquivo:
            arquivo.write("{")

        resultado = deposito.registrar_deposito(self.cpf, 10)

        self.assertIn("preparar o histórico", resultado)
        self.assertEqual(controle_saldo(self.cpf), 100.0)
        with open(self.caminho("depositos.json"), "r", encoding="utf-8") as arquivo:
            self.assertEqual(arquivo.read(), "{")


if __name__ == "__main__":
    unittest.main()
