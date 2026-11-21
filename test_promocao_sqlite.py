import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import migrar_financas_sqlite as migracao
import promocao_sqlite as promocao


OPENSSL = shutil.which("openssl")


@unittest.skipUnless(OPENSSL, "OpenSSL necessário para testes RSA")
class TestPromocaoSQLite(unittest.TestCase):
    cpf_a = "11144477735"
    cpf_b = "22457787823"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = self.root / "sources"
        self.sources.mkdir(mode=0o700)
        self.staging = self.root / "staging.sqlite3"
        self.manifest = self.root / "manifest.json"
        self.signature = self.root / "manifest.sig"
        self.private_key = self.root / "approver-private.pem"
        self.public_key = self.root / "approver-public.pem"
        self.registry = self.root / "approval-state.sqlite3"
        self._write_sources()
        self._create_staging()
        self._create_keys()
        self.environment = {
            "UNIRPIX_PROMOTION_PUBLIC_KEY_PATH": str(self.public_key),
            "UNIRPIX_PROMOTION_PUBLIC_KEY_ID": hashlib.sha256(self.public_key.read_bytes()).hexdigest(),
            "UNIRPIX_PROMOTION_OPENSSL_PATH": str(Path(OPENSSL).resolve()),
            "UNIRPIX_PROMOTION_OPENSSL_SHA256": hashlib.sha256(Path(OPENSSL).resolve().read_bytes()).hexdigest(),
            "UNIRPIX_PROMOTION_TARGET_ENV": "temporary-fixture",
            "UNIRPIX_PROMOTION_USAGE_DB": str(self.registry),
        }
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        promocao.inicializar_registro_uso_temporario(self.registry)
        self._sign_manifest()

    def tearDown(self):
        self.env_patch.stop()
        self.temp.cleanup()

    def _write_sources(self):
        docs = {
            "cadastro.json": [
                {
                    "cpf": self.cpf_a,
                    "nome": "Conta Fictícia Alfa",
                    "saldo": 25.50,
                    "senha_hash": f"scrypt:32768:8:1$fixtureSalt000001${'a' * 128}",
                },
                {"cpf": self.cpf_b, "nome": "Conta Fictícia Beta", "saldo": 0},
            ],
            "depositos.json": [
                {"cpf": self.cpf_a, "valor": 4.25, "data_hora": "2026-01-02T03:04:05+00:00"}
            ],
            "apostas.json": [
                {
                    "cpf": self.cpf_a,
                    "valor_apostado": 1,
                    "numero_escolhido": 5,
                    "numero_sorteado": 5,
                    "resultado": "Vitória!",
                    "valor_resultado": 10,
                }
            ],
        }
        for filename, records in docs.items():
            (self.sources / filename).write_text(
                json.dumps(records, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )

    def _source_paths(self):
        return {name: self.sources / name for name in migracao.ARQUIVOS_ORIGEM}

    def _create_staging(self):
        result = migracao.importar_jsons_temporarios(
            self._source_paths(), self.staging, dry_run=False
        )
        self.assertTrue(result.elegivel)
        self.assertFalse(result.ja_importado)

    def _create_keys(self):
        subprocess.run(
            [OPENSSL, "genrsa", "-out", str(self.private_key), "3072"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        subprocess.run(
            [OPENSSL, "rsa", "-in", str(self.private_key), "-pubout", "-out", str(self.public_key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        self.private_key.chmod(0o600)
        self.public_key.chmod(0o600)

    def _write_canonical_manifest(self, manifest):
        self.manifest.write_bytes(promocao.serializar_manifesto(manifest))
        self.manifest.chmod(0o600)

    def _sign_manifest(self):
        manifest = promocao.preparar_manifesto(
            self.staging,
            self._source_paths(),
            staging_id="fixture-staging-001",
        )
        public_bytes = self.public_key.read_bytes()
        manifest["approval"] = {
            "status": "approved",
            "approved_by": "admin-fixture",
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds"),
            "approval_id": str(uuid.uuid4()),
            "target_environment": "temporary-fixture",
            "key_id": hashlib.sha256(public_bytes).hexdigest(),
            "signature_algorithm": "RSA-SHA256",
        }
        self._write_canonical_manifest(manifest)
        subprocess.run(
            [
                OPENSSL, "dgst", "-sha256", "-sign", str(self.private_key),
                "-out", str(self.signature), str(self.manifest),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        self.signature.chmod(0o600)

    def _resign_mutated_manifest(self, mutation, private_key=None):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        mutation(manifest)
        self._write_canonical_manifest(manifest)
        subprocess.run(
            [
                OPENSSL, "dgst", "-sha256", "-sign", str(private_key or self.private_key),
                "-out", str(self.signature), str(self.manifest),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    def _validate(self, staging=None):
        return promocao.validar_staging(
            staging or self.staging,
            self.manifest,
            self.signature,
            self._source_paths(),
        )

    def test_manifesto_aprovado_valido_e_assinatura_independente(self):
        validated = self._validate()
        self.assertEqual(validated.staging, self.staging.resolve())
        self.assertEqual(validated.manifesto_sha256, hashlib.sha256(self.manifest.read_bytes()).hexdigest())

    def test_manifesto_adulterado_e_assinatura_invalida_sao_recusados(self):
        raw = self.manifest.read_bytes()
        altered = raw.replace(b"fixture-staging-001", b"fixture-staging-999")
        self.manifest.write_bytes(altered)
        with self.assertRaises(promocao.ErroPromocao):
            self._validate()

        self.manifest.write_bytes(raw)
        self.signature.write_bytes(b"assinatura-falsa")
        with self.assertRaisesRegex(promocao.ErroPromocao, "Assinatura"):
            self._validate()

    def test_manifesto_sem_aprovacao_assinada_e_recusado(self):
        self._sign_manifest()
        self._resign_mutated_manifest(lambda data: data.update(approval=None))
        with self.assertRaisesRegex(promocao.ErroPromocao, "aprovação"):
            self._validate()

    def test_staging_ausente_e_recusado(self):
        absent = self.root / "ausente.sqlite3"
        with self.assertRaises(promocao.ErroPromocao):
            self._validate(absent)

    def test_banco_corrompido_e_recusado(self):
        self.staging.write_bytes(self.staging.read_bytes()[:128])
        digest = hashlib.sha256(self.staging.read_bytes()).hexdigest()
        self._resign_mutated_manifest(
            lambda data: data["staging_database"].update(sha256=digest)
        )
        with self.assertRaisesRegex(promocao.ErroPromocao, "verificações SQLite"):
            self._validate()

    def test_esquema_incompativel_e_recusado(self):
        connection = sqlite3.connect(self.staging)
        try:
            connection.execute("DROP TRIGGER account_identity_is_immutable")
            connection.commit()
        finally:
            connection.close()
        database_hash = hashlib.sha256(self.staging.read_bytes()).hexdigest()
        with sqlite3.connect(self.staging) as connection:
            connection.row_factory = sqlite3.Row
            content_hash = promocao._fingerprint_banco(connection)
        self._resign_mutated_manifest(
            lambda data: data["staging_database"].update(
                sha256=database_hash, content_sha256=content_hash
            )
        )
        with self.assertRaises(promocao.ErroPromocao):
            self._validate()

    def test_dados_de_usuario_divergentes_das_fontes_sao_recusados(self):
        with sqlite3.connect(self.staging) as connection:
            connection.execute("UPDATE users SET nome='Conta Alterada' WHERE cpf=?", (self.cpf_a,))
            connection.commit()
        with self.assertRaisesRegex(promocao.ErroPromocao, "dados de usuário"):
            promocao.preparar_manifesto(self.staging, self._source_paths())

    def test_divergencias_de_contagens_e_fingerprint_sao_recusadas(self):
        self._resign_mutated_manifest(
            lambda data: data["counts"]["source"].update(users=99)
        )
        with self.assertRaises(promocao.ErroPromocao):
            self._validate()

        self._sign_manifest()
        self._resign_mutated_manifest(
            lambda data: data.update(batch_fingerprint="0" * 64)
        )
        with self.assertRaises(promocao.ErroPromocao):
            self._validate()

    def test_fontes_divergentes_sao_recusadas(self):
        source = self.sources / "depositos.json"
        source.write_text("[]", encoding="utf-8")
        with self.assertRaises(promocao.ErroPromocao):
            self._validate()

    def test_sources_contem_hash_sha256_e_tamanho_real_sem_dados_pessoais(self):
        manifest = promocao.preparar_manifesto(self.staging, self._source_paths())
        for name, path in self._source_paths().items():
            self.assertEqual(manifest["sources"][name]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(manifest["sources"][name]["size_bytes"], path.stat().st_size)
        encoded = promocao.serializar_manifesto(manifest).decode("utf-8")
        for forbidden in (self.cpf_a, self.cpf_b, "Conta Fictícia", "25.5", "4.25", "valor_apostado"):
            self.assertNotIn(forbidden, encoded)

    def test_manifesto_recusa_key_id_divergente_e_chave_confiavel_alternativa(self):
        self._resign_mutated_manifest(
            lambda data: data["approval"].update(key_id="0" * 64)
        )
        with self.assertRaisesRegex(promocao.ErroPromocao, "key_id|chave pública"):
            self._validate()
        self._sign_manifest()

        alternate_private = self.root / "alternate-private.pem"
        alternate_public = self.root / "alternate-public.pem"
        subprocess.run([OPENSSL, "genrsa", "-out", str(alternate_private), "3072"], check=True, timeout=15)
        subprocess.run([OPENSSL, "rsa", "-in", str(alternate_private), "-pubout", "-out", str(alternate_public)], check=True, timeout=15)
        alternate_private.chmod(0o600)
        alternate_public.chmod(0o600)
        with patch.dict(os.environ, {"UNIRPIX_PROMOTION_PUBLIC_KEY_PATH": str(alternate_public)}):
            with self.assertRaises(promocao.ErroPromocao):
                self._validate()

    def test_chave_rsa_abaixo_de_3072_bits_e_recusada(self):
        small_private = self.root / "small-private.pem"
        small_public = self.root / "small-public.pem"
        subprocess.run([OPENSSL, "genrsa", "-out", str(small_private), "2048"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        subprocess.run([OPENSSL, "rsa", "-in", str(small_private), "-pubout", "-out", str(small_public)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        small_public.chmod(0o600)
        self._resign_mutated_manifest(
            lambda data: data["approval"].update(key_id=hashlib.sha256(small_public.read_bytes()).hexdigest()),
            private_key=small_private,
        )
        with patch.dict(os.environ, {
            "UNIRPIX_PROMOTION_PUBLIC_KEY_PATH": str(small_public),
            "UNIRPIX_PROMOTION_PUBLIC_KEY_ID": hashlib.sha256(small_public.read_bytes()).hexdigest(),
        }):
            with self.assertRaisesRegex(promocao.ErroPromocao, "3072"):
                self._validate()

    def test_chave_nao_rsa_e_recusada(self):
        ec_private = self.root / "ec-private.pem"
        ec_public = self.root / "ec-public.pem"
        subprocess.run([OPENSSL, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(ec_private)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        subprocess.run([OPENSSL, "pkey", "-in", str(ec_private), "-pubout", "-out", str(ec_public)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        ec_public.chmod(0o600)
        self._resign_mutated_manifest(
            lambda data: data["approval"].update(key_id=hashlib.sha256(ec_public.read_bytes()).hexdigest()),
            private_key=ec_private,
        )
        with patch.dict(os.environ, {
            "UNIRPIX_PROMOTION_PUBLIC_KEY_PATH": str(ec_public),
            "UNIRPIX_PROMOTION_PUBLIC_KEY_ID": hashlib.sha256(ec_public.read_bytes()).hexdigest(),
        }):
            with self.assertRaisesRegex(promocao.ErroPromocao, "RSA"):
                self._validate()

    def test_openssl_falso_ausente_e_timeout_falham_sem_fallback(self):
        fake = self.root / "fake-openssl"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        with patch.dict(os.environ, {
            "UNIRPIX_PROMOTION_OPENSSL_PATH": str(fake),
            "UNIRPIX_PROMOTION_OPENSSL_SHA256": hashlib.sha256(fake.read_bytes()).hexdigest(),
        }):
            with self.assertRaises(promocao.ErroPromocao):
                self._validate()
        with patch.dict(os.environ, {"UNIRPIX_PROMOTION_OPENSSL_PATH": str(self.root / "missing-openssl")}):
            with self.assertRaises(promocao.ErroPromocao):
                self._validate()
        with patch.object(promocao.subprocess, "run", side_effect=subprocess.TimeoutExpired("openssl", 1)):
            with self.assertRaisesRegex(promocao.ErroPromocao, "verificar a assinatura"):
                self._validate()

    def test_usuario_inativo_e_chave_de_idempotencia_sao_recusados(self):
        with sqlite3.connect(self.staging) as connection:
            connection.execute("UPDATE users SET ativo=0 WHERE cpf=?", (self.cpf_a,))
        with self.assertRaisesRegex(promocao.ErroPromocao, "dados de usuário"):
            promocao.preparar_manifesto(self.staging, self._source_paths())
        with sqlite3.connect(self.staging) as connection:
            connection.execute("UPDATE users SET ativo=1 WHERE cpf=?", (self.cpf_a,))
            connection.execute(
                "INSERT INTO idempotency_keys(key_hash, user_id, operation_type, state, transaction_id, result_json, created_at) "
                "VALUES (?, 1, 'deposit', 'processing', NULL, NULL, '2026-01-01T00:00:00+00:00')",
                ("a" * 64,),
            )
        with self.assertRaises(promocao.ErroPromocao):
            promocao.preparar_manifesto(self.staging, self._source_paths())

    def test_aprovacao_expirada_ou_de_outro_ambiente_e_recusada(self):
        self._resign_mutated_manifest(lambda data: data["approval"].update(
            approved_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds"),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"),
        ))
        with self.assertRaisesRegex(promocao.ErroPromocao, "expirou"):
            self._validate()
        self._sign_manifest()
        self._resign_mutated_manifest(lambda data: data["approval"].update(target_environment="other"))
        with self.assertRaisesRegex(promocao.ErroPromocao, "ambiente"):
            self._validate()

    def test_replay_de_aprovacao_e_recusado(self):
        validated = self._validate()
        promocao.promover_staging(validated, self.root / "promovido.sqlite3")
        with self.assertRaisesRegex(promocao.ErroPromocao, "usada"):
            self._validate()

    def test_sidecar_preexistente_e_preservado(self):
        validated = self._validate()
        destination = self.root / "sidecar.sqlite3"
        sidecar = Path(str(destination) + "-wal")
        sidecar.write_bytes(b"preexisting sidecar")
        with self.assertRaises(promocao.ErroPromocao):
            promocao.promover_staging(validated, destination)
        self.assertEqual(sidecar.read_bytes(), b"preexisting sidecar")
        self.assertFalse(destination.exists())

    def test_sidecar_inserido_na_corrida_nao_e_removido(self):
        validated = self._validate()
        destination = self.root / "sidecar-race.sqlite3"
        sidecar = Path(str(destination) + "-wal")
        reserve_real = promocao._reservar_sidecars_proprios

        def race_before_reservation(path):
            sidecar.write_bytes(b"raced sidecar")
            return reserve_real(path)

        with patch.object(promocao, "_reservar_sidecars_proprios", side_effect=race_before_reservation):
            with self.assertRaises(promocao.ErroPromocao):
                promocao.promover_staging(validated, destination)
        self.assertEqual(sidecar.read_bytes(), b"raced sidecar")
        self.assertFalse(destination.exists())

    def test_limpeza_nao_remove_sidecar_sem_propriedade_comprovada(self):
        destination = self.root / "cleanup.sqlite3"
        backup_real = promocao._executar_backup
        sidecar = Path(str(destination) + "-wal")
        destination.write_bytes(b"destination created by this test")
        destination_info = destination.stat()
        sidecar.write_bytes(b"not owned by promotion")
        preserved = promocao._remover_destino_proprio(
            destination, (destination_info.st_dev, destination_info.st_ino), {}
        )
        self.assertIn(sidecar.name, preserved)
        self.assertEqual(sidecar.read_bytes(), b"not owned by promotion")
        self.assertFalse(destination.exists())

    def test_alteracao_concorrente_no_staging_durante_copia_faz_falhar(self):
        validated = self._validate()
        destination = self.root / "concurrent.sqlite3"
        backup_real = promocao._executar_backup

        def backup_then_mutate(source, target):
            backup_real(source, target)
            with sqlite3.connect(self.staging) as connection:
                connection.execute("UPDATE users SET nome='Changed concurrently' WHERE cpf=?", (self.cpf_a,))

        with patch.object(promocao, "_executar_backup", side_effect=backup_then_mutate):
            with self.assertRaises(promocao.ErroPromocao):
                promocao.promover_staging(validated, destination)
        self.assertFalse(destination.exists())
        self.assertTrue(self.staging.exists())

    def test_destino_preexistente_nao_e_sobrescrito(self):
        validated = self._validate()
        destination = self.root / "promovido.sqlite3"
        destination.write_bytes(b"nao sobrescrever")
        with self.assertRaisesRegex(promocao.ErroPromocao, "já existe"):
            promocao.promover_staging(validated, destination)
        self.assertEqual(destination.read_bytes(), b"nao sobrescrever")

    def test_copia_valida_preserva_staging_e_nao_sobrescreve(self):
        validated = self._validate()
        original_stage = self.staging.read_bytes()
        original_wal = Path(str(self.staging) + "-wal").read_bytes() if Path(str(self.staging) + "-wal").exists() else None
        destination = self.root / "promovido.sqlite3"
        result = promocao.promover_staging(validated, destination)
        self.assertEqual(result, destination.resolve())
        self.assertEqual(self.staging.read_bytes(), original_stage)
        current_wal_path = Path(str(self.staging) + "-wal")
        current_wal = current_wal_path.read_bytes() if current_wal_path.exists() else None
        self.assertEqual(current_wal, original_wal)
        promocao.persistencia.validar_esquema_banco(destination)
        with sqlite3.connect(destination) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(promocao._fingerprint_banco(connection), json.loads(self.manifest.read_text())["staging_database"]["content_sha256"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(promocao.ErroPromocao):
            promocao.promover_staging(validated, destination)

    def test_falha_durante_backup_remove_destino_e_preserva_staging(self):
        validated = self._validate()
        original_stage = self.staging.read_bytes()
        wal_path = Path(str(self.staging) + "-wal")
        original_wal = wal_path.read_bytes() if wal_path.exists() else None
        destination = self.root / "falha.sqlite3"
        backup_real = promocao._executar_backup

        def partial_then_fail(source, target):
            backup_real(source, target)
            raise sqlite3.OperationalError("falha de teste")

        with patch.object(promocao, "_executar_backup", side_effect=partial_then_fail):
            with self.assertRaises(promocao.ErroPromocao):
                promocao.promover_staging(validated, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(self.staging.read_bytes(), original_stage)
        current_wal = wal_path.read_bytes() if wal_path.exists() else None
        self.assertEqual(current_wal, original_wal)


if __name__ == "__main__":
    unittest.main()
