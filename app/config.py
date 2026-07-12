import os

from dotenv import load_dotenv


APP_ENV = os.getenv(
    "APP_ENV",
    os.getenv("FLASK_ENV", "development"),
).lower()

if APP_ENV not in {
    "production",
    "testing",
}:
    load_dotenv()

BASE_DIR = os.path.abspath(
    os.path.dirname(os.path.dirname(__file__))
)


def _bool_env(name, default=False):
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _database_url(default_sqlite=True):
    database_url = os.getenv("DATABASE_URL")

    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql://",
            1,
        )

    if database_url:
        return database_url

    if default_sqlite:
        return "sqlite:///" + os.path.join(
            BASE_DIR,
            "instance",
            "database.db",
        )

    return None


def _secret_key(required=False):
    secret_key = os.getenv("SECRET_KEY")

    if secret_key:
        return secret_key

    if required:
        raise RuntimeError(
            "SECRET_KEY not set in environment variables"
        )

    return "dev-only-secret-key"


class Config:

    ENV = APP_ENV

    DEBUG = _bool_env("DEBUG", False)
    TESTING = False

    SECRET_KEY = _secret_key(required=False)

    SQLALCHEMY_DATABASE_URI = _database_url()

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.getenv(
        "UPLOAD_FOLDER",
        os.path.join(
            BASE_DIR,
            "uploads"
        )
    )

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    AI_MOCK_MODE = _bool_env("AI_MOCK_MODE", False)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_SAMESITE = "Lax"

    ALLOWED_EXTENSIONS = {
        "pdf",
        "jpg",
        "jpeg",
        "png",
    }


class DevelopmentConfig(Config):

    DEBUG = _bool_env("DEBUG", True)


class ProductionConfig(Config):

    DEBUG = False
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url(default_sqlite=False)
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):

    TESTING = True
    DEBUG = False
    SECRET_KEY = os.getenv("SECRET_KEY", "test-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URL",
        "sqlite:///:memory:",
    )


def get_config():
    config_name = os.getenv(
        "APP_ENV",
        os.getenv("FLASK_ENV", "development"),
    ).lower()

    if config_name == "production":
        if not ProductionConfig.SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY not set in environment variables"
            )

        if not ProductionConfig.SQLALCHEMY_DATABASE_URI:
            raise RuntimeError(
                "DATABASE_URL not set in environment variables"
            )

        if ProductionConfig.SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
            raise RuntimeError(
                "Production DATABASE_URL must use PostgreSQL"
            )

        return ProductionConfig

    if config_name == "testing":
        return TestingConfig

    return DevelopmentConfig
