import os
import sys
import time

import psycopg


DEFAULT_RETRY_INTERVAL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 60
_MISSING = object()


def _database_url_from_environment():
    return os.environ.get("DATABASE_URL")


def _psycopg_database_url(database_url):
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://"):]

    return database_url


def wait_for_database(
    *,
    database_url=_MISSING,
    connect=psycopg.connect,
    sleep=time.sleep,
    retry_interval_seconds=DEFAULT_RETRY_INTERVAL_SECONDS,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
):
    if database_url is _MISSING:
        database_url = _database_url_from_environment()

    if not database_url:
        print("DATABASE_URL is required.")
        return 1

    safe_database_url = _psycopg_database_url(database_url)
    attempts = max(
        1,
        int(timeout_seconds // retry_interval_seconds) + 1,
    )

    for attempt in range(1, attempts + 1):
        try:
            connection = connect(safe_database_url)
            connection.close()
            print("Database ready.")
            return 0
        except psycopg.OperationalError:
            if attempt == attempts:
                print("Database was not ready before timeout.")
                return 1

            print("Database not ready; retrying...")
            sleep(retry_interval_seconds)
        except Exception:
            print("Database readiness check failed.")
            return 1

    return 1


def main():
    return wait_for_database()


if __name__ == "__main__":
    sys.exit(main())
