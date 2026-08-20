import importlib
import os
import tempfile
import unittest

from unittest.mock import PropertyMock, patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import (
    TestingConfig,
    development_database_path,
)
from app.extensions import db


class ProductionBootstrapTest(unittest.TestCase):

    def tearDown(self):
        import app.config as config_module

        importlib.reload(config_module)

    def test_health_check_is_public_and_does_not_touch_database(self):
        app = create_app(TestingConfig)
        client = app.test_client()

        with patch.object(db, "session") as session_mock, patch(
            "app.services.ai.ai_service.get_openai_client",
        ) as openai_mock, patch(
            "app.services.notifications.email_service.send_email_reminder",
        ) as resend_mock:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(response.get_json(), {"status": "ok"})
        session_mock.assert_not_called()
        openai_mock.assert_not_called()
        resend_mock.assert_not_called()

    def test_create_app_does_not_create_tables_on_startup(self):
        with patch.object(db, "create_all") as create_all_mock:
            create_app(TestingConfig)

        create_all_mock.assert_not_called()

    def test_flask_migrate_is_initialized(self):
        app = create_app(TestingConfig)

        self.assertIn("migrate", app.extensions)

    def test_local_storage_creates_upload_folder_on_boot(self):
        class LocalStorageConfig(TestingConfig):
            STORAGE_BACKEND = "local"
            UPLOAD_FOLDER = os.path.join(
                tempfile.gettempdir(),
                "buildsure-local-uploads-test",
            )

        with patch("app.os.makedirs") as makedirs_mock:
            create_app(LocalStorageConfig)

        makedirs_mock.assert_called_once_with(
            LocalStorageConfig.UPLOAD_FOLDER,
            exist_ok=True,
        )

    def test_s3_storage_does_not_create_upload_folder_on_boot(self):
        class S3StorageConfig(TestingConfig):
            STORAGE_BACKEND = "s3"
            UPLOAD_FOLDER = os.path.join(
                tempfile.gettempdir(),
                "buildsure-s3-uploads-should-not-be-created",
            )

        with patch("app.os.makedirs") as makedirs_mock, patch(
            "app.services.documents.storage.S3Storage.client",
            new_callable=PropertyMock,
        ) as storage_client:
            app = create_app(S3StorageConfig)
            response = app.test_client().get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        makedirs_mock.assert_not_called()
        storage_client.assert_not_called()

    def test_main_is_official_wsgi_entrypoint(self):
        main = importlib.import_module("main")

        self.assertTrue(hasattr(main, "app"))
        self.assertEqual(main.app.import_name, "app")

        with open("Procfile", encoding="utf-8") as procfile:
            self.assertEqual(
                procfile.read().strip(),
                (
                    "web: gunicorn --bind 0.0.0.0:$PORT --workers 1 "
                    "--threads 4 --timeout 120 --access-logfile - "
                    "--error-logfile - main:app"
                ),
            )

    def test_main_import_does_not_require_external_service_keys(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite:///:memory:",
                "SECRET_KEY": "test-secret",
            },
            clear=True,
        ), patch(
            "app.extensions.scheduler.start",
        ) as scheduler_start:
            import main

            reloaded = importlib.reload(main)

        self.assertTrue(hasattr(reloaded, "app"))
        scheduler_start.assert_not_called()

    def test_testing_config_uses_safe_cookie_defaults(self):
        app = create_app(TestingConfig)

        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    def test_development_config_uses_local_defaults(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "DATABASE_URL": "",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)
            config = reloaded.get_config()

        self.assertTrue(config.DEBUG)
        self.assertFalse(config.TESTING)
        self.assertIn("instance", config.SQLALCHEMY_DATABASE_URI)
        self.assertTrue(
            config.SQLALCHEMY_DATABASE_URI.startswith("sqlite:///")
        )

    def test_testing_config_uses_isolated_database(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "testing",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)
            config = reloaded.get_config()

        self.assertTrue(config.TESTING)
        self.assertFalse(config.DEBUG)
        self.assertEqual(config.SQLALCHEMY_DATABASE_URI, "sqlite:///:memory:")

    def test_testing_config_rejects_development_database_url(self):
        development_uri = "sqlite:///" + development_database_path()

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "testing",
                "TEST_DATABASE_URL": development_uri,
            },
            clear=True,
        ):
            import app.config as config_module

            with self.assertRaisesRegex(
                RuntimeError,
                "Refusing to run destructive test database operation",
            ):
                importlib.reload(config_module)

    def test_create_app_rejects_testing_app_pointing_to_development_database(self):
        class UnsafeTestingConfig(TestingConfig):
            TESTING = True
            SQLALCHEMY_DATABASE_URI = (
                "sqlite:///" + development_database_path()
            )

        with self.assertRaisesRegex(
            RuntimeError,
            "Refusing to run destructive test database operation",
        ):
            create_app(UnsafeTestingConfig)

    def test_boolean_environment_values_are_parsed_explicitly(self):
        with patch.dict(
            os.environ,
            {
                "DEBUG": "false",
                "AI_MOCK_MODE": "0",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)
            self.assertFalse(reloaded._bool_env("DEBUG", True))
            self.assertFalse(reloaded._bool_env("AI_MOCK_MODE", True))

        with patch.dict(
            os.environ,
            {
                "DEBUG": "true",
                "AI_MOCK_MODE": "1",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)
            self.assertTrue(reloaded._bool_env("DEBUG", False))
            self.assertTrue(reloaded._bool_env("AI_MOCK_MODE", False))

    def test_production_config_requires_secret_and_database_url(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)

            with self.assertRaises(RuntimeError):
                reloaded.get_config()

    def test_production_config_fails_without_database_url(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)

            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                reloaded.get_config()

    def test_production_config_sets_secure_cookies_and_debug_false(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": "postgres://example",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)
            config = reloaded.get_config()

        self.assertFalse(config.DEBUG)
        self.assertTrue(config.SESSION_COOKIE_SECURE)
        self.assertTrue(config.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(config.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertTrue(config.REMEMBER_COOKIE_SECURE)
        self.assertTrue(config.REMEMBER_COOKIE_HTTPONLY)
        self.assertEqual(config.REMEMBER_COOKIE_SAMESITE, "Lax")
        self.assertTrue(config.RATELIMIT_ENABLED)
        self.assertIn("default-src", config.CONTENT_SECURITY_POLICY)
        self.assertEqual(
            config.SQLALCHEMY_DATABASE_URI,
            "postgresql+psycopg://example",
        )

    def test_production_config_rejects_local_document_storage_by_default(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": "postgres://example",
                "STORAGE_BACKEND": "local",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)

            with self.assertRaisesRegex(RuntimeError, "persistent storage"):
                reloaded.get_config()

    def test_production_config_allows_local_storage_only_with_override(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": "postgres://example",
                "STORAGE_BACKEND": "local",
                "ALLOW_LOCAL_STORAGE_IN_PRODUCTION": "true",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)
            config = reloaded.get_config()

        self.assertEqual(config.STORAGE_BACKEND, "local")

    def test_openai_module_import_does_not_require_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            import app.services.ai.ai_service as ai_service

            reloaded = importlib.reload(ai_service)

        self.assertTrue(hasattr(reloaded, "get_openai_client"))

    def test_openai_client_raises_only_when_used_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            import app.services.ai.ai_service as ai_service

            reloaded = importlib.reload(ai_service)

            with self.assertRaises(RuntimeError):
                reloaded.get_openai_client()

    def test_openai_client_is_created_only_on_first_use(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
            },
            clear=True,
        ):
            import app.services.ai.ai_service as ai_service

            reloaded = importlib.reload(ai_service)

            with patch.object(reloaded, "OpenAI") as openai_class:
                reloaded.get_openai_client()

        openai_class.assert_called_once_with(api_key="test-key")

    def test_ai_mock_mode_does_not_require_openai_key(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "testing",
                "AI_MOCK_MODE": "true",
            },
            clear=True,
        ):
            import app.config as config_module

            config = importlib.reload(config_module).get_config()

        self.assertTrue(config.AI_MOCK_MODE)
        self.assertIsNone(config.OPENAI_API_KEY)

    def test_run_uses_configured_debug_and_scheduler_opt_in(self):
        import run

        self.assertFalse(run._scheduler_enabled())
        self.assertEqual(run.app.debug, run.app.config["DEBUG"])

    def test_scheduler_is_disabled_by_default_and_in_testing(self):
        with patch.dict(os.environ, {}, clear=True):
            import run

            self.assertFalse(run._scheduler_enabled())

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "testing",
            },
            clear=True,
        ):
            import run

            self.assertFalse(run._scheduler_enabled())


if __name__ == "__main__":
    unittest.main()
