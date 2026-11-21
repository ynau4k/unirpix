import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import aposta
import controle_saldo as modulo_saldo
from controle_saldo import controle_saldo


class TestAposta(unittest.TestCase):
    cpf = "00000000000"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.diretorio_original = os.getcwd()
        self.diretorio_dados_original = modulo_saldo.DIRETORIO_DADOS
        modulo_saldo.DIRETORIO_DADOS = Path(self.tempdir.name)
        self.diretorio_execucao = Path(self.tempdir.name) / "execucao"
        self.diretorio_execucao.mkdir()
        os.chdir(self.diretorio_execucao)
        with open(self.caminho("cadastro.json"), "w", encoding="utf-8") as arquivo:
            json.dump([{"cpf": self.cpf, "saldo": 100.0}], arquivo)

    def tearDown(self):
        os.chdir(self.diretorio_original)
        modulo_saldo.DIRETORIO_DADOS = self.diretorio_dados_original
        self.tempdir.cleanup()

    def caminho(self, nome):
        return Path(self.tempdir.name) / nome

    def carregar_saldo(self):
        return controle_saldo(self.cpf)

    def carregar_historico(self):
        with open(self.caminho("apostas.json"), "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def executar(self, entradas, numero_sorteado):
        with patch("builtins.input", side_effect=entradas), patch(
            "aposta.random.randint", return_value=numero_sorteado
        ) as sorteio:
            resultado = aposta.apostar()
        return resultado, sorteio

    def test_importar_modulo_nao_inicia_aposta(self):
        processo = subprocess.run(
            [sys.executable, "-c", "import aposta, cadastro, deposito"],
            cwd=os.path.dirname(os.path.abspath(aposta.__file__)),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(processo.returncode, 0, processo.stderr)
        self.assertEqual(processo.stdout, "")

    def test_vitoria_credita_lucro_liquido_de_dez_vezes_e_preserva_historico(self):
        historico_antigo = [{"resultado": "registro anterior"}]
        with open(self.caminho("apostas.json"), "w", encoding="utf-8") as arquivo:
            json.dump(historico_antigo, arquivo)

        resultado, sorteio = self.executar([self.cpf, "5,00", "7"], 7)

        self.assertIn("Lucro líquido ou perda: R$ 50.00", resultado)
        self.assertEqual(self.carregar_saldo(), 150.0)
        self.assertEqual(sorteio.call_count, 1)
        historico = self.carregar_historico()
        self.assertEqual(len(historico), 2)
        self.assertEqual(historico[-1]["valor_resultado"], 50.0)
        self.assertEqual(historico[-1]["numero_escolhido"], 7)
        self.assertEqual(
            set(historico[-1]),
            {
                "cpf",
                "valor_apostado",
                "numero_escolhido",
                "numero_sorteado",
                "resultado",
                "valor_resultado",
                "data_hora",
            },
        )
        datetime.fromisoformat(historico[-1]["data_hora"])

    def test_derrota_debita_aposta(self):
        resultado, _ = self.executar([self.cpf, "7", "2"], 3)

        self.assertIn("Lucro líquido ou perda: R$ -7.00", resultado)
        self.assertEqual(self.carregar_saldo(), 93.0)
        self.assertEqual(self.carregar_historico()[0]["valor_resultado"], -7.0)

    def test_servico_de_aposta_nao_solicita_entrada_terminal(self):
        with patch("builtins.input", side_effect=AssertionError("input não esperado")), patch(
            "aposta.random.randint", return_value=8
        ):
            resultado = aposta.registrar_aposta(self.cpf, 4, 8)

        self.assertIn("Lucro líquido ou perda: R$ 40.00", resultado)
        self.assertEqual(self.carregar_saldo(), 140.0)

    def test_entradas_invalidas_sao_repetidas_e_numero_e_validado_antes_do_sorteio(self):
        eventos = []

        def ler_entrada(_prompt):
            eventos.append("input")
            return next(entradas)

        entradas = iter(
            [self.cpf, "texto", "nan", "inf", "-1", "0", "5", "11", "x", "4"]
        )

        def sortear(_inicio, _fim):
            eventos.append("sorteio")
            return 4

        with patch("builtins.input", side_effect=ler_entrada), patch(
            "aposta.random.randint", side_effect=sortear
        ) as sorteio:
            aposta.apostar()

        self.assertEqual(sorteio.call_count, 1)
        self.assertEqual(eventos[-2:], ["input", "sorteio"])
        self.assertEqual(self.carregar_saldo(), 150.0)

    def test_historico_invalido_impede_alteracao_no_saldo(self):
        with open(self.caminho("apostas.json"), "w", encoding="utf-8") as arquivo:
            arquivo.write("{")

        resultado, sorteio = self.executar([self.cpf, "5", "1"], 1)

        self.assertIn("preparar o histórico", resultado)
        self.assertEqual(self.carregar_saldo(), 100.0)
        self.assertEqual(sorteio.call_count, 1)

    def test_cadastro_json_invalido_e_tratado_sem_sorteio(self):
        with open(self.caminho("cadastro.json"), "w", encoding="utf-8") as arquivo:
            arquivo.write("{")

        with patch("builtins.input", return_value=self.cpf), patch(
            "aposta.random.randint"
        ) as sorteio:
            resultado = aposta.apostar()

        self.assertIn("consultar o saldo", resultado)
        self.assertEqual(sorteio.call_count, 0)

    def test_retorno_falso_de_atualizar_saldo_nao_registra_aposta(self):
        with patch("aposta.atualizar_saldo", return_value=False):
            resultado, _ = self.executar([self.cpf, "5", "1"], 1)

        self.assertIn("saldo", resultado.lower())
        self.assertEqual(self.carregar_saldo(), 100.0)
        self.assertFalse(self.caminho("apostas.json").exists())

    def test_erro_de_atualizacao_nao_registra_aposta(self):
        with patch("aposta.atualizar_saldo", side_effect=OSError("falha simulada")):
            resultado, _ = self.executar([self.cpf, "5", "1"], 1)

        self.assertIn("atualização do saldo", resultado)
        self.assertEqual(self.carregar_saldo(), 100.0)
        self.assertFalse(self.caminho("apostas.json").exists())

    def test_falha_ao_substituir_historico_reverte_saldo(self):
        with patch("aposta.finalizar_json", side_effect=OSError("falha simulada")):
            resultado, _ = self.executar([self.cpf, "5", "1"], 1)

        self.assertIn("saldo foi revertido", resultado)
        self.assertEqual(self.carregar_saldo(), 100.0)
        self.assertFalse(self.caminho("apostas.json").exists())
        self.assertFalse(any(nome.endswith(".tmp") for nome in os.listdir(self.tempdir.name)))

    def test_valor_acima_do_saldo_nao_sorteia_nem_registra(self):
        resultado, sorteio = self.executar([self.cpf, "101"], 1)

        self.assertIn("Saldo insuficiente", resultado)
        self.assertEqual(self.carregar_saldo(), 100.0)
        self.assertEqual(sorteio.call_count, 0)
        self.assertFalse(self.caminho("apostas.json").exists())

    def test_reconfere_saldo_antes_de_debitar_uma_derrota(self):
        with patch("aposta.controle_saldo", side_effect=[100.0, 3.0]), patch(
            "aposta.atualizar_saldo"
        ) as atualizar:
            resultado, _ = self.executar([self.cpf, "5", "2"], 3)

        self.assertIn("Saldo insuficiente", resultado)
        atualizar.assert_not_called()
        self.assertEqual(self.carregar_saldo(), 100.0)
        self.assertFalse(self.caminho("apostas.json").exists())


if __name__ == "__main__":
    unittest.main()
