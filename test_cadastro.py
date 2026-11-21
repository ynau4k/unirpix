import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cadastro
import controle_saldo as modulo_saldo
from werkzeug.security import check_password_hash


class TestCadastro(unittest.TestCase):
    cpf = "11144477735"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.diretorio_dados_original = modulo_saldo.DIRETORIO_DADOS
        modulo_saldo.DIRETORIO_DADOS = Path(self.tempdir.name)
        self.diretorio_execucao_original = os.getcwd()
        self.diretorio_execucao = Path(self.tempdir.name) / "execucao"
        self.diretorio_execucao.mkdir()
        os.chdir(self.diretorio_execucao)

    def tearDown(self):
        os.chdir(self.diretorio_execucao_original)
        modulo_saldo.DIRETORIO_DADOS = self.diretorio_dados_original
        self.tempdir.cleanup()

    def caminho(self, nome):
        return Path(self.tempdir.name) / nome

    def ler_usuarios(self):
        with open(self.caminho("cadastro.json"), "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def test_cadastra_usuario_com_hash_sem_input(self):
        with patch("builtins.input", side_effect=AssertionError("input não esperado")):
            resultado = cadastro.cadastrar_usuario(
                "Pessoa de Teste", self.cpf, "senha-de-teste-segura"
            )

        self.assertEqual(resultado, "Cadastro realizado com sucesso!")
        usuarios = self.ler_usuarios()
        self.assertEqual(set(usuarios[0]), {"nome", "cpf", "saldo", "senha_hash"})
        self.assertEqual(usuarios[0]["nome"], "Pessoa de Teste")
        self.assertEqual(usuarios[0]["cpf"], self.cpf)
        self.assertEqual(usuarios[0]["saldo"], 0)
        self.assertNotEqual(usuarios[0]["senha_hash"], "senha-de-teste-segura")
        self.assertTrue(check_password_hash(usuarios[0]["senha_hash"], "senha-de-teste-segura"))

    def test_interface_de_terminal_continua_cadastrando(self):
        with patch("builtins.input", side_effect=["Pessoa de Teste", self.cpf]), patch(
            "cadastro.getpass", side_effect=["senha-de-teste-segura", "senha-de-teste-segura"]
        ):
            resultado = cadastro.cadastrar()

        self.assertEqual(resultado, "Cadastro realizado com sucesso!")
        self.assertEqual(self.ler_usuarios()[0]["cpf"], self.cpf)

    def test_cadastro_preserva_registros_e_rejeita_duplicata(self):
        anterior = [{"nome": "Anterior", "cpf": "22457787823", "saldo": 12.0}]
        with open(self.caminho("cadastro.json"), "w", encoding="utf-8") as arquivo:
            json.dump(anterior, arquivo)

        resultado = cadastro.cadastrar_usuario("Nova Pessoa", self.cpf, "senha-de-teste-segura")
        duplicado = cadastro.cadastrar_usuario("Outro Nome", self.cpf, "outra-senha-segura")

        self.assertEqual(resultado, "Cadastro realizado com sucesso!")
        self.assertEqual(duplicado, "CPF já cadastrado!")
        usuarios = self.ler_usuarios()
        self.assertEqual(usuarios[0], anterior[0])
        self.assertEqual(len(usuarios), 2)

    def test_cpf_invalido_nao_cria_arquivo(self):
        resultado = cadastro.cadastrar_usuario("Pessoa de Teste", "123", "senha-de-teste-segura")

        self.assertEqual(resultado, "CPF inválido!")
        self.assertFalse(self.caminho("cadastro.json").exists())

    def test_erro_de_leitura_do_json_e_reportado(self):
        with open(self.caminho("cadastro.json"), "w", encoding="utf-8") as arquivo:
            arquivo.write("{")

        resultado = cadastro.cadastrar_usuario("Pessoa de Teste", self.cpf, "senha-de-teste-segura")

        self.assertIn("Não foi possível ler", resultado)
        with open(self.caminho("cadastro.json"), "r", encoding="utf-8") as arquivo:
            self.assertEqual(arquivo.read(), "{")


if __name__ == "__main__":
    unittest.main()
