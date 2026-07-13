import importlib
import os
import tempfile
import unittest

from datetime import date
from unittest.mock import patch

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User


EXPECTED_TABLES = {
    "alembic_version",
    "document",
    "project",
    "project_subcontractor",
    "subcontractor",
    "user",
}

MODEL_TABLES = {
    "document",
    "project",
    "project_subcontractor",
    "subcontractor",
    "user",
}


class DatabaseMigrationTest(unittest.TestCase):

    def tearDown(self):
        import app.config as config_module

        importlib.reload(config_module)

    def test_database_url_normalizes_postgres_scheme(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": "postgres://user:pass@example.com/db",
            },
            clear=True,
        ):
            import app.config as config_module

            config = importlib.reload(config_module).get_config()

        self.assertEqual(
            config.SQLALCHEMY_DATABASE_URI,
            "postgresql+psycopg://user:pass@example.com/db",
        )

    def test_production_accepts_postgresql_database_url(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": "postgresql://user:pass@example.com/db",
            },
            clear=True,
        ):
            import app.config as config_module

            config = importlib.reload(config_module).get_config()

        self.assertEqual(
            config.SQLALCHEMY_DATABASE_URI,
            "postgresql+psycopg://user:pass@example.com/db",
        )
        self.assertFalse(config.DEBUG)

    def test_production_rejects_sqlite_database_url(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "prod-secret",
                "DATABASE_URL": "sqlite:///prod.db",
            },
            clear=True,
        ):
            import app.config as config_module

            reloaded = importlib.reload(config_module)

            with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                reloaded.get_config()

    def test_testing_config_does_not_use_real_database_url(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "testing",
                "DATABASE_URL": "postgresql://prod.example.com/db",
            },
            clear=True,
        ):
            import app.config as config_module

            config = importlib.reload(config_module).get_config()

        self.assertEqual(config.SQLALCHEMY_DATABASE_URI, "sqlite:///:memory:")

    def test_model_metadata_contains_expected_tables(self):
        app = create_app(TestingConfig)

        with app.app_context():
            self.assertEqual(set(db.metadata.tables.keys()), MODEL_TABLES)

    def test_create_app_does_not_create_schema_automatically(self):
        app = create_app(TestingConfig)

        with app.app_context():
            inspector = inspect(db.engine)
            self.assertEqual(inspector.get_table_names(), [])

    def test_migration_upgrade_creates_expected_schema_and_constraints(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                inspector = inspect(db.engine)
                self.assertEqual(set(inspector.get_table_names()), EXPECTED_TABLES)

                unique_constraints = inspector.get_unique_constraints(
                    "project_subcontractor"
                )
                self.assertIn(
                    "unique_project_sub",
                    {
                        constraint["name"]
                        for constraint in unique_constraints
                    },
                )

                document_foreign_keys = inspector.get_foreign_keys("document")
                constrained_columns = {
                    tuple(foreign_key["constrained_columns"])
                    for foreign_key in document_foreign_keys
                }
                self.assertIn(("project_id",), constrained_columns)
                self.assertIn(("sub_id",), constrained_columns)
                self.assertIn(("uploaded_by",), constrained_columns)

                document_indexes = {
                    index["name"]
                    for index in inspector.get_indexes("document")
                }
                self.assertIn("ix_document_ai_status", document_indexes)
                self.assertIn("ix_document_uploaded_at", document_indexes)

    def test_migrated_schema_columns_match_metadata(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                inspector = inspect(db.engine)

                for table_name in MODEL_TABLES:
                    with self.subTest(table=table_name):
                        metadata_columns = db.metadata.tables[table_name].columns
                        migrated_columns = {
                            column["name"]: column
                            for column in inspector.get_columns(table_name)
                        }

                        self.assertEqual(
                            set(migrated_columns.keys()),
                            set(metadata_columns.keys()),
                        )

                        for column_name, metadata_column in metadata_columns.items():
                            migrated_column = migrated_columns[column_name]
                            self.assertEqual(
                                migrated_column["nullable"],
                                metadata_column.nullable,
                            )

    def test_metadata_tables_compile_for_postgresql_dialect(self):
        app = create_app(TestingConfig)
        dialect = postgresql.dialect()

        with app.app_context():
            compiled_tables = {
                table_name: str(
                    CreateTable(table).compile(dialect=dialect)
                )
                for table_name, table in db.metadata.tables.items()
            }

        self.assertIn('"user"', compiled_tables["user"])
        self.assertIn("JSON", compiled_tables["document"])
        self.assertIn("BOOLEAN", compiled_tables["user"])
        self.assertIn(
            "CONSTRAINT unique_project_sub",
            compiled_tables["project_subcontractor"],
        )

    def test_app_can_use_schema_after_migration_upgrade(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                user = User(email="owner@example.com", paid=True)
                user.set_password("password123")
                db.session.add(user)
                db.session.flush()

                project = Project(name="Migrated Project", user_id=user.id)
                subcontractor = Subcontractor(
                    name="Migrated Sub",
                    user_id=user.id,
                    coi_expiration=date.today(),
                )
                db.session.add_all([project, subcontractor])
                db.session.flush()

                link = ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=subcontractor.id,
                )
                document = Document(
                    filename="coi.pdf",
                    original_name="coi.pdf",
                    document_type="COI",
                    sub_id=subcontractor.id,
                    uploaded_by=user.id,
                )
                db.session.add_all([link, document])
                db.session.commit()

                self.assertEqual(User.query.count(), 1)
                self.assertEqual(Project.query.count(), 1)
                self.assertEqual(Subcontractor.query.count(), 1)
                self.assertEqual(ProjectSubcontractor.query.count(), 1)
                self.assertEqual(Document.query.count(), 1)

    def test_migration_downgrade_base_removes_schema(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                downgrade(directory="migrations", revision="base")
                inspector = inspect(db.engine)
                self.assertEqual(
                    set(inspector.get_table_names()),
                    {"alembic_version"},
                )

    def temporary_migrated_app(self):
        return TemporaryMigratedApp()


class TemporaryMigratedApp:

    def __enter__(self):
        self.database = tempfile.NamedTemporaryFile(
            suffix=".sqlite",
            delete=False,
        )
        self.database.close()

        database_uri = "sqlite:///" + self.database.name.replace("\\", "/")

        class TempMigrationConfig(TestingConfig):
            SQLALCHEMY_DATABASE_URI = database_uri

        self.app = create_app(TempMigrationConfig)

        with self.app.app_context():
            upgrade(directory="migrations")

        return self.app

    def __exit__(self, exc_type, exc, tb):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

        try:
            os.unlink(self.database.name)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
