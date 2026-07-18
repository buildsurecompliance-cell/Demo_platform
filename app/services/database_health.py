from dataclasses import dataclass

import click

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import current_app
from flask_migrate import upgrade
from sqlalchemy import inspect

from app.extensions import db


TECHNICAL_TABLES = {
    "alembic_version",
}


@dataclass(frozen=True)
class DatabaseHealth:
    database_uri: str
    dialect: str
    expected_tables: list[str]
    existing_tables: list[str]
    missing_tables: list[str]
    unexpected_tables: list[str]
    current_revision: str | None
    migration_head: str | None

    @property
    def healthy(self):
        return (
            not self.missing_tables
            and not self.unexpected_tables
            and self.current_revision == self.migration_head
        )


def collect_database_health():
    expected_tables = sorted(db.metadata.tables.keys())
    inspector = inspect(db.engine)
    existing_tables = sorted(inspector.get_table_names())
    application_existing_tables = {
        table
        for table in existing_tables
        if table not in TECHNICAL_TABLES
    }
    expected_set = set(expected_tables)

    return DatabaseHealth(
        database_uri=_masked_database_uri(),
        dialect=db.engine.dialect.name,
        expected_tables=expected_tables,
        existing_tables=existing_tables,
        missing_tables=sorted(expected_set - application_existing_tables),
        unexpected_tables=sorted(application_existing_tables - expected_set),
        current_revision=_current_revision(),
        migration_head=_migration_head(),
    )


def register_database_cli(app):
    @app.cli.command("db-health")
    def db_health_command():
        health = collect_database_health()
        _echo_database_health(health)

        if not health.healthy:
            raise click.ClickException("Database schema is unhealthy.")

    @app.cli.command("init-local-db")
    @click.option("--create-demo-user", is_flag=True)
    @click.option("--generate-demo-environment", is_flag=True)
    def init_local_db_command(create_demo_user, generate_demo_environment):
        if current_app.config.get("ENV") == "production":
            raise click.ClickException(
                "init-local-db is disabled in production."
            )

        upgrade(directory="migrations")
        health = collect_database_health()
        _echo_database_health(health)

        if not health.healthy:
            raise click.ClickException("Database schema is unhealthy.")

        if create_demo_user:
            _create_demo_user_once()

        if generate_demo_environment:
            from app.services.demo_project_generator.project_generator import (
                generate_demo_environment as generate_environment,
            )

            generate_environment(
                preset="full-demo",
                seed=123,
                scenario="mixed",
                create_records=True,
            )

        db.session.commit()


def _echo_database_health(health):
    click.echo(f"Database: {health.database_uri}")
    click.echo(f"Dialect: {health.dialect}")
    click.echo(f"Current revision: {health.current_revision or 'none'}")
    click.echo(f"Migration head: {health.migration_head or 'none'}")
    click.echo(f"Expected tables: {len(health.expected_tables)}")
    click.echo(f"Existing tables: {len(health.existing_tables)}")
    click.echo(
        "Missing tables: "
        + (", ".join(health.missing_tables) or "none")
    )
    click.echo(
        "Unexpected tables: "
        + (", ".join(health.unexpected_tables) or "none")
    )
    click.echo(f"Status: {'HEALTHY' if health.healthy else 'UNHEALTHY'}")


def _masked_database_uri():
    return db.engine.url.render_as_string(hide_password=True)


def _current_revision():
    with db.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return context.get_current_revision()


def _migration_head():
    alembic_config = Config("migrations/alembic.ini")
    alembic_config.set_main_option("script_location", "migrations")
    script = ScriptDirectory.from_config(alembic_config)
    return script.get_current_head()


def _create_demo_user_once():
    from app.models import User
    from app.services.organizations import create_default_organization_for_user

    user = User.query.filter_by(email="demo@buildsure.local").first()

    if user:
        return user

    user = User(
        email="demo@buildsure.local",
        paid=True,
    )
    user.set_password("demo-password")
    db.session.add(user)
    db.session.flush()
    create_default_organization_for_user(user)
    current_app.logger.info("Local demo user created.")
    return user
