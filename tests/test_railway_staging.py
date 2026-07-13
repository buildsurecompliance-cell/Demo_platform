import importlib
import json
import os
import unittest

from pathlib import Path
from unittest.mock import PropertyMock, patch
from urllib.parse import urlparse


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db


ROOT = Path(__file__).resolve().parents[1]
START_COMMAND = (
    "gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 "
    "--timeout 120 --access-logfile - --error-logfile - main:app"
)


class RailwayStagingTest(unittest.TestCase):

    def tearDown(self):
        import app.config as config_module

        importlib.reload(config_module)

    def test_railway_json_is_valid_and_uses_expected_commands(self):
        config = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))

        self.assertEqual(
            config["$schema"],
            "https://railway.com/railway.schema.json",
        )
        self.assertEqual(config["build"]["builder"], "NIXPACKS")
        self.assertEqual(config["deploy"]["startCommand"], START_COMMAND)
        self.assertEqual(config["deploy"]["preDeployCommand"], "flask db upgrade")
        self.assertEqual(config["deploy"]["healthcheckPath"], "/health")
        self.assertEqual(config["deploy"]["restartPolicyType"], "ON_FAILURE")
        self.assertEqual(config["deploy"]["restartPolicyMaxRetries"], 10)
        self.assertNotIn("flask db upgrade", config["deploy"]["startCommand"])

    def test_railway_json_uses_expected_top_level_structure(self):
        config = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))

        self.assertEqual(set(config.keys()), {"$schema", "build", "deploy"})
        self.assertEqual(set(config["build"].keys()), {"builder"})
        self.assertEqual(
            set(config["deploy"].keys()),
            {
                "preDeployCommand",
                "startCommand",
                "healthcheckPath",
                "healthcheckTimeout",
                "restartPolicyType",
                "restartPolicyMaxRetries",
            },
        )
        self.assertIsInstance(config["deploy"]["preDeployCommand"], str)
        self.assertIsInstance(config["deploy"]["startCommand"], str)

    def test_procfile_matches_railway_start_command(self):
        procfile = (ROOT / "Procfile").read_text(encoding="utf-8").strip()

        self.assertEqual(procfile, f"web: {START_COMMAND}")

    def test_procfile_has_canonical_casing_in_git(self):
        import subprocess

        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        procfiles = [
            line
            for line in result.stdout.splitlines()
            if line.lower() == "procfile"
        ]

        self.assertEqual(procfiles, ["Procfile"])

    def test_production_config_reads_railway_port_and_preserves_database_url(self):
        database_url = (
            "postgres://user:pass@containers-us-west-1.railway.app:5432/railway"
            "?sslmode=require&connect_timeout=10"
        )

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "PORT": "12345",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": database_url,
                "STORAGE_BACKEND": "s3",
                "AI_MOCK_MODE": "true",
                "SCHEDULER_ENABLED": "false",
            },
            clear=True,
        ):
            import app.config as config_module

            config = importlib.reload(config_module).get_config()

        self.assertEqual(config.PORT, 12345)
        self.assertEqual(
            config.SQLALCHEMY_DATABASE_URI,
            database_url.replace("postgres://", "postgresql+psycopg://", 1),
        )
        self.assertTrue(config.SESSION_COOKIE_SECURE)
        self.assertTrue(config.REMEMBER_COOKIE_SECURE)
        self.assertTrue(config.AI_MOCK_MODE)

    def test_postgresql_url_with_encoded_password_and_query_is_preserved(self):
        database_url = (
            "postgresql://railway:user%40pa%3Ass@containers.railway.app:5432"
            "/railway?sslmode=require&application_name=buildsure"
        )

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": database_url,
                "STORAGE_BACKEND": "s3",
            },
            clear=True,
        ):
            import app.config as config_module

            config = importlib.reload(config_module).get_config()

        parsed = urlparse(config.SQLALCHEMY_DATABASE_URI)
        self.assertEqual(parsed.scheme, "postgresql+psycopg")
        self.assertEqual(parsed.username, "railway")
        self.assertEqual(parsed.password, "user%40pa%3Ass")
        self.assertEqual(parsed.hostname, "containers.railway.app")
        self.assertEqual(parsed.port, 5432)
        self.assertEqual(parsed.path, "/railway")
        self.assertEqual(
            parsed.query,
            "sslmode=require&application_name=buildsure",
        )

    def test_env_example_contains_placeholders_without_real_secrets(self):
        content = (ROOT / ".env.example").read_text(encoding="utf-8")

        required_names = {
            "APP_ENV",
            "SECRET_KEY",
            "DATABASE_URL",
            "OPENAI_API_KEY",
            "RESEND_API_KEY",
            "AI_MOCK_MODE",
            "SCHEDULER_ENABLED",
            "STORAGE_BACKEND",
            "S3_BUCKET",
            "S3_REGION",
            "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "S3_PRESIGNED_URL_TTL",
            "RATELIMIT_ENABLED",
            "LOGIN_RATE_LIMIT",
            "REGISTER_RATE_LIMIT",
        }
        actual_names = {
            line.split("=", 1)[0]
            for line in content.splitlines()
            if line and not line.startswith("#")
        }

        self.assertTrue(required_names.issubset(actual_names))
        self.assertNotIn("sk-", content)
        self.assertNotIn("AKIA", content)
        self.assertNotIn("railway.internal", content)
        self.assertNotIn("postgresql://", content)

    def test_env_example_names_match_configured_environment_variables(self):
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        actual_names = {
            line.split("=", 1)[0]
            for line in content.splitlines()
            if line and not line.startswith("#")
        }
        allowed_names = {
            "APP_ENV",
            "SECRET_KEY",
            "DATABASE_URL",
            "OPENAI_API_KEY",
            "RESEND_API_KEY",
            "AI_MOCK_MODE",
            "SCHEDULER_ENABLED",
            "STORAGE_BACKEND",
            "S3_BUCKET",
            "S3_REGION",
            "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "S3_PRESIGNED_URL_TTL",
            "S3_PUBLIC_BASE_URL",
            "RATELIMIT_ENABLED",
            "LOGIN_RATE_LIMIT",
            "REGISTER_RATE_LIMIT",
        }

        self.assertEqual(actual_names, allowed_names)
        self.assertNotIn("ALLOW_LOCAL_STORAGE_IN_PRODUCTION", actual_names)
        self.assertIn("LOGIN_RATE_LIMIT=5 per minute", content)
        self.assertIn("REGISTER_RATE_LIMIT=3 per minute", content)

    def test_health_check_is_independent_for_staging_bootstrap(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        with patch.object(db, "session") as session_mock, patch(
            "app.services.ai.ai_service.get_openai_client",
        ) as openai_mock, patch(
            "app.services.documents.storage.S3Storage.client",
            new_callable=PropertyMock,
        ) as storage_client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        session_mock.assert_not_called()
        openai_mock.assert_not_called()
        storage_client.assert_not_called()

    def test_create_app_does_not_run_migrations_or_scheduler_on_import_path(self):
        class RailwayLikeConfig(TestingConfig):
            STORAGE_BACKEND = "s3"
            AI_MOCK_MODE = True
            PORT = 12345

        with patch.dict(
            os.environ,
            {
                "PORT": "12345",
                "SCHEDULER_ENABLED": "false",
            },
            clear=True,
        ), patch.object(db, "create_all") as create_all_mock, patch(
            "app.extensions.scheduler.start",
        ) as scheduler_start, patch(
            "app.services.documents.storage.S3Storage.client",
            new_callable=PropertyMock,
        ) as storage_client:
            app = create_app(RailwayLikeConfig)

        self.assertEqual(app.config["STORAGE_BACKEND"], "s3")
        create_all_mock.assert_not_called()
        scheduler_start.assert_not_called()
        storage_client.assert_not_called()

    def test_s3_storage_is_lazy_until_first_use(self):
        class S3TestingConfig(TestingConfig):
            STORAGE_BACKEND = "s3"

        app = create_app(S3TestingConfig)

        with app.app_context():
            from app.services.documents.storage import S3Storage, get_document_storage

            storage = get_document_storage()

        self.assertIsInstance(storage, S3Storage)
        self.assertIsNone(storage._client)

    def test_scheduler_is_disabled_for_staging_example(self):
        with patch.dict(
            os.environ,
            {
                "SCHEDULER_ENABLED": "false",
            },
            clear=True,
        ):
            import run

            reloaded = importlib.reload(run)

        self.assertFalse(reloaded._scheduler_enabled())


if __name__ == "__main__":
    unittest.main()
