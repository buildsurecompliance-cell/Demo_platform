import io
import unittest

from contextlib import redirect_stdout
from unittest.mock import Mock

import psycopg

from scripts.wait_for_database import wait_for_database


class WaitForDatabaseTest(unittest.TestCase):

    def run_wait(self, **kwargs):
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = wait_for_database(
                retry_interval_seconds=5,
                timeout_seconds=10,
                **kwargs,
            )

        return exit_code, output.getvalue()

    def test_immediate_success_closes_connection(self):
        connection = Mock()
        connect = Mock(return_value=connection)

        exit_code, output = self.run_wait(
            database_url="postgresql://user:secret@example.com/db",
            connect=connect,
            sleep=Mock(),
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Database ready.", output)
        connect.assert_called_once_with(
            "postgresql://user:secret@example.com/db"
        )
        connection.close.assert_called_once()

    def test_initial_operational_error_retries_then_succeeds(self):
        connection = Mock()
        connect = Mock(
            side_effect=[
                psycopg.OperationalError("database system is starting up"),
                connection,
            ]
        )
        sleep = Mock()

        exit_code, output = self.run_wait(
            database_url="postgres://user:secret@example.com/db",
            connect=connect,
            sleep=sleep,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Database not ready; retrying...", output)
        self.assertIn("Database ready.", output)
        self.assertEqual(connect.call_count, 2)
        connect.assert_any_call("postgresql://user:secret@example.com/db")
        sleep.assert_called_once_with(5)
        connection.close.assert_called_once()

    def test_timeout_returns_nonzero(self):
        connect = Mock(
            side_effect=psycopg.OperationalError(
                "database system is starting up"
            )
        )
        sleep = Mock()

        exit_code, output = self.run_wait(
            database_url="postgresql://user:secret@example.com/db",
            connect=connect,
            sleep=sleep,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("Database was not ready before timeout.", output)
        self.assertEqual(connect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_missing_database_url_fails_safely(self):
        connect = Mock()

        exit_code, output = self.run_wait(
            database_url="",
            connect=connect,
            sleep=Mock(),
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("DATABASE_URL is required.", output)
        connect.assert_not_called()

    def test_credentials_are_never_printed(self):
        connect = Mock(
            side_effect=psycopg.OperationalError(
                "database system is starting up"
            )
        )

        exit_code, output = self.run_wait(
            database_url="postgresql://user:secret@example.com/db",
            connect=connect,
            sleep=Mock(),
        )

        self.assertEqual(exit_code, 1)
        self.assertNotIn("postgresql://", output)
        self.assertNotIn("user", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("example.com", output)


if __name__ == "__main__":
    unittest.main()
