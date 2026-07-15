import importlib
import os
import tempfile
import unittest

from datetime import date
from unittest.mock import patch

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    Document,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectSubcontractor,
    Subcontractor,
    User,
)
from app.services.organizations import create_default_organization_for_user


EXPECTED_TABLES = {
    "alembic_version",
    "document",
    "organization",
    "organization_invitation",
    "organization_membership",
    "project",
    "project_subcontractor",
    "subcontractor",
    "user",
}

MODEL_TABLES = {
    "document",
    "organization",
    "organization_invitation",
    "organization_membership",
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

                membership_constraints = inspector.get_unique_constraints(
                    "organization_membership"
                )
                self.assertIn(
                    "unique_organization_user_membership",
                    {
                        constraint["name"]
                        for constraint in membership_constraints
                    },
                )

                invitation_indexes = {
                    index["name"]
                    for index in inspector.get_indexes(
                        "organization_invitation"
                    )
                }
                self.assertIn(
                    "ix_organization_invitation_token_hash",
                    invitation_indexes,
                )
                self.assertIn(
                    "unique_pending_organization_invitation",
                    invitation_indexes,
                )

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
        self.assertIn(
            "CONSTRAINT unique_organization_user_membership",
            compiled_tables["organization_membership"],
        )

    def test_app_can_use_schema_after_migration_upgrade(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                user = User(email="owner@example.com", paid=True)
                user.set_password("password123")
                db.session.add(user)
                db.session.flush()
                organization = create_default_organization_for_user(user)

                project = Project(
                    name="Migrated Project",
                    user_id=user.id,
                    organization_id=organization.id,
                )
                subcontractor = Subcontractor(
                    name="Migrated Sub",
                    user_id=user.id,
                    organization_id=organization.id,
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

    def test_migration_upgrade_assigns_existing_data_to_organizations(self):
        with self.temporary_migrated_app_from_revision(
            "ebe17429fa03"
        ) as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (email, password_hash, paid, timezone)
                        VALUES
                            ('legacy@example.com', 'hash', 1, 'US/Eastern')
                        """
                    )
                )
                user_id = db.session.execute(
                    text("SELECT id FROM user WHERE email = 'legacy@example.com'")
                ).scalar_one()
                db.session.execute(
                    text(
                        """
                        INSERT INTO project (name, contract_value, user_id)
                        VALUES ('Legacy Project', 0, :user_id)
                        """
                    ),
                    {"user_id": user_id},
                )
                project_id = db.session.execute(
                    text("SELECT id FROM project WHERE name = 'Legacy Project'")
                ).scalar_one()
                db.session.execute(
                    text(
                        """
                        INSERT INTO subcontractor (name, user_id)
                        VALUES ('Legacy Sub', :user_id)
                        """
                    ),
                    {"user_id": user_id},
                )
                subcontractor_id = db.session.execute(
                    text(
                        "SELECT id FROM subcontractor WHERE name = 'Legacy Sub'"
                    )
                ).scalar_one()
                db.session.commit()

                upgrade(directory="migrations")

                self.assertEqual(Organization.query.count(), 1)
                self.assertEqual(OrganizationMembership.query.count(), 1)

                membership = OrganizationMembership.query.one()
                self.assertEqual(membership.user_id, user_id)
                self.assertEqual(membership.role, "OWNER")

                migrated_project = Project.query.get(project_id)
                migrated_sub = Subcontractor.query.get(subcontractor_id)
                self.assertEqual(
                    migrated_project.organization_id,
                    membership.organization_id,
                )
                self.assertEqual(
                    migrated_sub.organization_id,
                    membership.organization_id,
                )

    def test_migration_upgrade_keeps_tenant_data_distinct(self):
        with self.temporary_migrated_app_from_revision(
            "ebe17429fa03"
        ) as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (email, password_hash, paid, timezone)
                        VALUES
                            ('one@example.com', 'hash', 1, 'US/Eastern'),
                            ('two@example.com', 'hash', 1, 'US/Eastern')
                        """
                    )
                )
                user_rows = db.session.execute(
                    text("SELECT id, email FROM user ORDER BY id")
                ).fetchall()

                for user in user_rows:
                    db.session.execute(
                        text(
                            """
                            INSERT INTO project
                                (name, contract_value, user_id)
                            VALUES
                                (:name, 0, :user_id)
                            """
                        ),
                        {
                            "name": f"Project {user.email}",
                            "user_id": user.id,
                        },
                    )
                    db.session.execute(
                        text(
                            """
                            INSERT INTO subcontractor (name, user_id)
                            VALUES (:name, :user_id)
                            """
                        ),
                        {
                            "name": f"Sub {user.email}",
                            "user_id": user.id,
                        },
                    )

                db.session.commit()

                upgrade(directory="migrations")

                self.assertEqual(Organization.query.count(), 2)
                self.assertEqual(OrganizationMembership.query.count(), 2)
                self.assertEqual(
                    Project.query.filter(
                        Project.organization_id.is_(None)
                    ).count(),
                    0,
                )
                self.assertEqual(
                    Subcontractor.query.filter(
                        Subcontractor.organization_id.is_(None)
                    ).count(),
                    0,
                )

                for membership in OrganizationMembership.query.all():
                    self.assertEqual(
                        Project.query.filter_by(
                            user_id=membership.user_id,
                            organization_id=membership.organization_id,
                        ).count(),
                        1,
                    )
                    self.assertEqual(
                        Subcontractor.query.filter_by(
                            user_id=membership.user_id,
                            organization_id=membership.organization_id,
                        ).count(),
                        1,
                    )

    def test_final_tenancy_migration_backfills_deterministic_null_rows(self):
        with self.temporary_migrated_app_from_revision(
            "7c2b8d91f0a4"
        ) as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (id, email, password_hash, paid, timezone)
                        VALUES
                            (1001, 'single-member@example.com', 'hash', 1, 'UTC')
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization
                            (id, name, created_at, updated_at)
                        VALUES
                            (2001, 'Single Member Org', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization_membership
                            (organization_id, user_id, role, created_at)
                        VALUES
                            (2001, 1001, 'OWNER', CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO project
                            (id, name, contract_value, user_id, organization_id)
                        VALUES
                            (3001, 'Needs Project Backfill', 0, 1001, NULL)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO subcontractor
                            (id, name, user_id, organization_id)
                        VALUES
                            (4001, 'Needs Sub Backfill', 1001, NULL)
                        """
                    )
                )
                db.session.commit()

                upgrade(directory="migrations")

                self.assertEqual(
                    db.session.execute(
                        text(
                            """
                            SELECT organization_id
                            FROM project
                            WHERE id = 3001
                            """
                        )
                    ).scalar_one(),
                    2001,
                )
                self.assertEqual(
                    db.session.execute(
                        text(
                            """
                            SELECT organization_id
                            FROM subcontractor
                            WHERE id = 4001
                            """
                        )
                    ).scalar_one(),
                    2001,
                )

    def test_final_tenancy_migration_fails_on_ambiguous_null_rows(self):
        with self.temporary_migrated_app_from_revision(
            "7c2b8d91f0a4"
        ) as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (id, email, password_hash, paid, timezone)
                        VALUES
                            (1001, 'deterministic-before-ambiguous@example.com', 'hash', 1, 'UTC'),
                            (1002, 'ambiguous@example.com', 'hash', 1, 'UTC')
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization
                            (id, name, created_at, updated_at)
                        VALUES
                            (2001, 'Deterministic Org', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                            (2101, 'First Org', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                            (2102, 'Second Org', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO organization_membership
                            (organization_id, user_id, role, created_at)
                        VALUES
                            (2001, 1001, 'OWNER', CURRENT_TIMESTAMP),
                            (2101, 1002, 'OWNER', CURRENT_TIMESTAMP),
                            (2102, 1002, 'MEMBER', CURRENT_TIMESTAMP)
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO project
                            (id, name, contract_value, user_id, organization_id)
                        VALUES
                            (3001, 'Should Not Partially Backfill', 0, 1001, NULL),
                            (3101, 'Ambiguous Project', 0, 1002, NULL)
                        """
                    )
                )
                db.session.commit()

                with self.assertRaises(SystemExit):
                    upgrade(directory="migrations")

                self.assertEqual(
                    db.session.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM project
                            WHERE id = 3101
                            """
                        )
                    ).scalar_one(),
                    1,
                )
                self.assertIsNone(
                    db.session.execute(
                        text(
                            """
                            SELECT organization_id
                            FROM project
                            WHERE id = 3001
                            """
                        )
                    ).scalar_one()
                )

    def test_final_tenancy_migration_fails_on_missing_membership(self):
        with self.temporary_migrated_app_from_revision(
            "7c2b8d91f0a4"
        ) as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (id, email, password_hash, paid, timezone)
                        VALUES
                            (1003, 'missing-membership@example.com', 'hash', 1, 'UTC')
                        """
                    )
                )
                db.session.execute(
                    text(
                        """
                        INSERT INTO project
                            (id, name, contract_value, user_id, organization_id)
                        VALUES
                            (3201, 'No Membership Project', 0, 1003, NULL)
                        """
                    )
                )
                db.session.commit()

                with self.assertRaises(SystemExit):
                    upgrade(directory="migrations")

                self.assertEqual(
                    db.session.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM project
                            WHERE id = 3201
                            """
                        )
                    ).scalar_one(),
                    1,
                )

    def test_final_tenancy_migration_downgrade_only_reopens_nullability(self):
        with self.temporary_migrated_app() as app:
            with app.app_context():
                downgrade(directory="migrations", revision="7c2b8d91f0a4")
                inspector = inspect(db.engine)

                self.assertIn("organization", inspector.get_table_names())
                self.assertIn(
                    "organization_membership",
                    inspector.get_table_names(),
                )
                project_columns = {
                    column["name"]: column
                    for column in inspector.get_columns("project")
                }
                subcontractor_columns = {
                    column["name"]: column
                    for column in inspector.get_columns("subcontractor")
                }
                self.assertTrue(project_columns["organization_id"]["nullable"])
                self.assertTrue(
                    subcontractor_columns["organization_id"]["nullable"]
                )

    def test_last_active_organization_migration_is_nullable_and_downgrades(self):
        with self.temporary_migrated_app_from_revision("4a9f1c2d3e5b") as app:
            with app.app_context():
                db.session.execute(
                    text(
                        """
                        INSERT INTO user
                            (id, email, password_hash, paid, timezone)
                        VALUES
                            (8101, 'last-active@example.com', 'hash', 1, 'UTC')
                        """
                    )
                )
                db.session.commit()

                upgrade(directory="migrations")
                inspector = inspect(db.engine)
                user_columns = {
                    column["name"]: column
                    for column in inspector.get_columns("user")
                }

                self.assertIn("last_active_organization_id", user_columns)
                self.assertTrue(
                    user_columns["last_active_organization_id"]["nullable"]
                )
                self.assertIsNone(
                    db.session.execute(
                        text(
                            """
                            SELECT last_active_organization_id
                            FROM user
                            WHERE id = 8101
                            """
                        )
                    ).scalar_one()
                )

                downgrade(directory="migrations", revision="4a9f1c2d3e5b")
                inspector = inspect(db.engine)
                user_columns = {
                    column["name"]
                    for column in inspector.get_columns("user")
                }

                self.assertNotIn("last_active_organization_id", user_columns)
                self.assertEqual(
                    db.session.execute(
                        text("SELECT COUNT(*) FROM user WHERE id = 8101")
                    ).scalar_one(),
                    1,
                )

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

    def temporary_migrated_app_from_revision(self, revision):
        return TemporaryMigratedApp(revision=revision)


class TemporaryMigratedApp:

    def __init__(self, revision=None):
        self.revision = revision

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
            upgrade(
                directory="migrations",
                revision=self.revision or "head",
            )

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
