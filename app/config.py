import os
import sys

from datetime import timedelta
from urllib.parse import unquote

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
            "postgresql+psycopg://",
            1,
        )

    if database_url and database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
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


def _sqlite_uri_path(database_uri):
    if not database_uri or database_uri == "sqlite:///:memory:":
        return None

    if not database_uri.startswith("sqlite:///"):
        return None

    path = unquote(database_uri[len("sqlite:///"):])

    if path.startswith("/") and len(path) > 3 and path[2] == ":":
        path = path[1:]

    return os.path.abspath(os.path.normpath(path))


def development_database_path():
    return os.path.abspath(
        os.path.normpath(
            os.path.join(
                BASE_DIR,
                "instance",
                "database.db",
            )
        )
    )


def is_development_database_uri(database_uri):
    sqlite_path = _sqlite_uri_path(database_uri)

    return bool(
        sqlite_path
        and os.path.normcase(sqlite_path)
        == os.path.normcase(development_database_path())
    )


def assert_safe_test_database_uri(database_uri):
    if is_development_database_uri(database_uri):
        raise RuntimeError(
            "Refusing to run destructive test database operation "
            "against development database."
        )


def is_test_process():
    test_command_names = {
        "pytest",
        "pytest.exe",
        "py.test",
        "py.test.exe",
        "unittest",
        "unittest.py",
    }

    for arg in sys.argv:
        normalized = os.path.normpath(arg).lower()
        command_name = os.path.basename(normalized)

        if command_name in test_command_names:
            return True

        path_parts = set(normalized.split(os.sep))

        if "unittest" in path_parts and command_name == "__main__.py":
            return True

    return bool(
        os.getenv("PYTEST_CURRENT_TEST")
    )


def assert_safe_runtime_database_uri(database_uri):
    if is_test_process():
        assert_safe_test_database_uri(database_uri)


def _testing_database_url():
    database_uri = os.getenv(
        "TEST_DATABASE_URL",
        "sqlite:///:memory:",
    )
    assert_safe_test_database_uri(database_uri)
    return database_uri


def _secret_key(required=False):
    secret_key = os.getenv("SECRET_KEY")

    if secret_key:
        return secret_key

    if required:
        raise RuntimeError(
            "SECRET_KEY not set in environment variables"
        )

    return "dev-only-secret-key"


def _int_env(name, default):
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


class Config:

    ENV = APP_ENV

    DEBUG = _bool_env("DEBUG", False)
    TESTING = False
    PORT = _int_env("PORT", 8000)
    APPLICATION_BASE_URL = os.getenv(
        "APPLICATION_BASE_URL",
        os.getenv("APP_BASE_URL", "http://localhost:8000"),
    )
    DOCUMENT_REQUEST_EXPIRATION_DAYS = _int_env(
        "DOCUMENT_REQUEST_EXPIRATION_DAYS",
        7,
    )

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

    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
    S3_BUCKET = os.getenv("S3_BUCKET")
    S3_REGION = os.getenv("S3_REGION")
    S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
    S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
    S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY")
    S3_PUBLIC_BASE_URL = os.getenv("S3_PUBLIC_BASE_URL")
    S3_PRESIGNED_URL_TTL = _int_env("S3_PRESIGNED_URL_TTL", 300)
    ALLOW_LOCAL_STORAGE_IN_PRODUCTION = _bool_env(
        "ALLOW_LOCAL_STORAGE_IN_PRODUCTION",
        False,
    )

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    AI_MOCK_MODE = _bool_env("AI_MOCK_MODE", False)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = False
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(
        days=_int_env("PERMANENT_SESSION_DAYS", 7)
    )
    WTF_CSRF_ENABLED = _bool_env("WTF_CSRF_ENABLED", True)

    CONTENT_SECURITY_POLICY = os.getenv(
        "CONTENT_SECURITY_POLICY",
        (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
    )
    X_FRAME_OPTIONS = os.getenv("X_FRAME_OPTIONS", "SAMEORIGIN")
    REFERRER_POLICY = os.getenv(
        "REFERRER_POLICY",
        "strict-origin-when-cross-origin",
    )
    PERMISSIONS_POLICY = os.getenv(
        "PERMISSIONS_POLICY",
        "camera=(), microphone=(), geolocation=()",
    )

    RATELIMIT_ENABLED = _bool_env(
        "RATELIMIT_ENABLED",
        APP_ENV == "production",
    )
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5 per minute")
    REGISTER_RATE_LIMIT = os.getenv("REGISTER_RATE_LIMIT", "3 per minute")
    DOCUMENT_REQUEST_UPLOAD_RATE_LIMIT = os.getenv(
        "DOCUMENT_REQUEST_UPLOAD_RATE_LIMIT",
        "10 per hour",
    )

    SUBSCRIPTION_GRACE_PERIOD_DAYS = _int_env(
        "SUBSCRIPTION_GRACE_PERIOD_DAYS",
        7,
    )
    ALLOW_LEGACY_ORGANIZATIONS_WITHOUT_SUBSCRIPTION = _bool_env(
        "ALLOW_LEGACY_ORGANIZATIONS_WITHOUT_SUBSCRIPTION",
        False,
    )
    BILLING_PROVIDER = os.getenv("BILLING_PROVIDER", "internal")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
    STRIPE_STARTER_PRICE_ID = os.getenv("STRIPE_STARTER_PRICE_ID")
    STRIPE_PROFESSIONAL_PRICE_ID = os.getenv("STRIPE_PROFESSIONAL_PRICE_ID")
    STRIPE_CHECKOUT_MODE = os.getenv("STRIPE_CHECKOUT_MODE", "subscription")
    STRIPE_API_VERSION = os.getenv("STRIPE_API_VERSION")
    BILLING_SUCCESS_URL = os.getenv("BILLING_SUCCESS_URL")
    BILLING_CANCEL_URL = os.getenv("BILLING_CANCEL_URL")
    BILLING_PORTAL_RETURN_URL = os.getenv("BILLING_PORTAL_RETURN_URL")
    BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS = _int_env(
        "BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS",
        300,
    )
    BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS = _int_env(
        "BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS",
        300,
    )

    ALLOWED_EXTENSIONS = {
        "pdf",
        "jpg",
        "jpeg",
        "png",
    }
    DANGEROUS_UPLOAD_EXTENSIONS = {
        "bat",
        "cmd",
        "com",
        "exe",
        "html",
        "htm",
        "js",
        "php",
        "ps1",
        "sh",
        "svg",
        "vbs",
    }


class DevelopmentConfig(Config):

    DEBUG = _bool_env("DEBUG", True)


class ProductionConfig(Config):

    DEBUG = False
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url(default_sqlite=False)
    SESSION_COOKIE_SECURE = True
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3").lower()
    REMEMBER_COOKIE_SECURE = True


class TestingConfig(Config):

    TESTING = True
    DEBUG = False
    SECRET_KEY = os.getenv("SECRET_KEY", "test-secret")
    SQLALCHEMY_DATABASE_URI = _testing_database_url()


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

        if (
            ProductionConfig.STORAGE_BACKEND == "local"
            and not ProductionConfig.ALLOW_LOCAL_STORAGE_IN_PRODUCTION
        ):
            raise RuntimeError(
                "Production document storage must use persistent storage"
            )

        return ProductionConfig

    if config_name == "testing":
        return TestingConfig

    return DevelopmentConfig
