import os
import re
import unittest

from io import BytesIO
from unittest.mock import patch

from flask import abort


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Document, Project, Subcontractor, User
from app.routes.subcontractors import allowed_file
from app.security import reset_rate_limits


class SecurityHardeningTest(unittest.TestCase):

    def setUp(self):
        reset_rate_limits()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=True,
            PROPAGATE_EXCEPTIONS=False,
            RATELIMIT_ENABLED=False,
        )

        @self.app.route("/test-forbidden")
        def test_forbidden():
            abort(403)

        @self.app.route("/test-error")
        def test_error():
            raise RuntimeError("secret stack trace should not be exposed")

        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            self.user = User(email="owner@example.com", paid=True)
            self.user.set_password("password123")
            self.other_user = User(email="other@example.com", paid=True)
            self.other_user.set_password("password123")
            db.session.add_all([self.user, self.other_user])
            db.session.commit()
            self.user_id = self.user.id
            self.other_user_id = self.other_user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

        reset_rate_limits()

    def csrf_token(self, path="/login", client=None):
        client = client or self.client
        response = client.get(path)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def assert_security_headers(self, response):
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("strict-origin", response.headers["Referrer-Policy"])
        self.assertIn("camera=()", response.headers["Permissions-Policy"])
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])

    def login_session(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_post_without_csrf_token_fails(self):
        response = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"Traceback", response.data)

    def test_post_with_csrf_token_works(self):
        token = self.csrf_token()
        response = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)

    def test_register_with_valid_csrf_token_works(self):
        token = self.csrf_token("/register")
        response = self.client.post(
            "/register",
            data={
                "email": "new@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        with self.app.app_context():
            self.assertIsNotNone(User.query.filter_by(email="new@example.com").first())

    def test_subscribe_requires_csrf_and_accepts_valid_token(self):
        missing = self.client.post(
            "/subscribe",
            data={"email": "buyer@example.com"},
        )

        token = self.csrf_token("/login")
        valid = self.client.post(
            "/subscribe",
            data={
                "email": "buyer@example.com",
                "csrf_token": token,
            },
        )

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(valid.status_code, 302)
        self.assertIn("/register", valid.location)

    def test_invalid_csrf_token_uses_safe_error(self):
        response = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": "invalid-token",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Request blocked", response.data)
        self.assertNotIn(b"invalid-token", response.data)
        self.assertNotIn(b"Traceback", response.data)

    def test_health_does_not_require_csrf(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_delete_requires_csrf_token(self):
        with self.app.app_context():
            sub = Subcontractor(name="Owned Sub", user_id=self.user_id)
            db.session.add(sub)
            db.session.flush()
            document = Document(
                filename="missing.pdf",
                original_name="missing.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.user_id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login_session(self.user_id)

        response = self.client.post(f"/delete_document/{document_id}")

        self.assertEqual(response.status_code, 403)

    def test_delete_with_csrf_token_works_after_ownership_check(self):
        token = self.csrf_token()

        with self.app.app_context():
            sub = Subcontractor(name="Owned Sub", user_id=self.user_id)
            db.session.add(sub)
            db.session.flush()
            document = Document(
                filename="missing.pdf",
                original_name="missing.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.user_id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login_session(self.user_id)

        response = self.client.post(
            f"/delete_document/{document_id}",
            data={"csrf_token": token},
        )

        self.assertEqual(response.status_code, 302)

    def test_login_next_local_is_allowed(self):
        token = self.csrf_token()
        response = self.client.post(
            "/login?next=/dashboard",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/dashboard")

    def test_login_next_external_host_is_rejected(self):
        token = self.csrf_token()
        response = self.client.post(
            "/login?next=https://example.com/phish",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)
        self.assertNotIn("example.com", response.location)

    def test_login_next_protocol_relative_is_rejected(self):
        token = self.csrf_token()
        response = self.client.post(
            "/login?next=//example.com/phish",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)
        self.assertNotIn("example.com", response.location)

    def test_login_next_backslash_is_rejected(self):
        token = self.csrf_token()
        response = self.client.post(
            r"/login?next=\example.com\phish",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)
        self.assertNotIn("example.com", response.location)

    def test_login_next_encoded_slash_and_backslash_are_rejected(self):
        unsafe_targets = [
            "/%2fevil.com/path",
            "/%5cevil.com%5cpath",
            "%5c%5cevil.com",
        ]

        for target in unsafe_targets:
            token = self.csrf_token()
            response = self.client.post(
                f"/login?next={target}",
                data={
                    "email": "owner@example.com",
                    "password": "password123",
                    "csrf_token": token,
                },
            )

            self.assertEqual(response.status_code, 302)
            self.assertIn("/dashboard", response.location)
            self.assertNotIn("evil.com", response.location)

    def test_login_next_rejects_schemes_userinfo_ports_and_subdomains(self):
        unsafe_targets = [
            "javascript:alert(1)",
            "data:text/html,hello",
            "http://localhost@evil.com/path",
            "http://localhost:9999/dashboard",
            "https://localhost.evil.com/dashboard",
        ]

        for target in unsafe_targets:
            token = self.csrf_token()
            response = self.client.post(
                f"/login?next={target}",
                data={
                    "email": "owner@example.com",
                    "password": "password123",
                    "csrf_token": token,
                },
            )

            self.assertEqual(response.status_code, 302)
            self.assertIn("/dashboard", response.location)

    def test_login_next_allows_safe_local_targets(self):
        safe_targets = [
            "/dashboard",
            "/project/1",
            "dashboard",
        ]

        for target in safe_targets:
            token = self.csrf_token()
            response = self.client.post(
                f"/login?next={target}",
                data={
                    "email": "owner@example.com",
                    "password": "password123",
                    "csrf_token": token,
                },
            )

            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, target)

    def test_security_headers_are_present(self):
        response = self.client.get("/login")

        self.assert_security_headers(response)

    def test_health_also_receives_security_headers(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assert_security_headers(response)

    def test_security_headers_are_present_on_redirects_and_errors(self):
        token = self.csrf_token()
        redirect_response = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "password123",
                "csrf_token": token,
            },
        )
        not_found = self.client.get("/missing-page")
        forbidden = self.client.get("/test-forbidden")
        server_error = self.client.get("/test-error")

        self.app.config["RATELIMIT_ENABLED"] = True
        self.app.config["LOGIN_RATE_LIMIT"] = "0 per minute"
        rate_token = self.csrf_token()
        rate_limited = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "wrong-password",
                "csrf_token": rate_token,
            },
        )

        for response in [
            redirect_response,
            not_found,
            forbidden,
            server_error,
            rate_limited,
        ]:
            self.assert_security_headers(response)

    def test_error_handlers_do_not_expose_stack_trace(self):
        response = self.client.get("/test-error")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b"Traceback", response.data)
        self.assertNotIn(b"secret stack trace", response.data)
        self.assertIn(b"Something went wrong", response.data)

    def test_500_handler_rolls_back_database_session(self):
        with patch.object(db.session, "rollback") as rollback_mock:
            response = self.client.get("/test-error")

        self.assertEqual(response.status_code, 500)
        rollback_mock.assert_called()

    def test_404_and_403_handlers_are_custom(self):
        not_found = self.client.get("/missing-page")
        forbidden = self.client.get("/test-forbidden")

        self.assertEqual(not_found.status_code, 404)
        self.assertIn(b"Page not found", not_found.data)
        self.assertEqual(forbidden.status_code, 403)
        self.assertIn(b"Access denied", forbidden.data)

    def test_cookie_config_for_environments(self):
        testing_app = create_app(TestingConfig)

        self.assertTrue(testing_app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertFalse(testing_app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(testing_app.config["REMEMBER_COOKIE_HTTPONLY"])
        self.assertFalse(testing_app.config["REMEMBER_COOKIE_SECURE"])

    def test_upload_hardening_blocks_dangerous_names(self):
        with self.app.app_context():
            self.assertFalse(allowed_file(""))
            self.assertFalse(allowed_file("no_extension"))
            self.assertFalse(allowed_file("payload.svg"))
            self.assertFalse(allowed_file("payload.SVG"))
            self.assertFalse(allowed_file("payload.html"))
            self.assertFalse(allowed_file("invoice.html.pdf"))
            self.assertFalse(allowed_file("invoice.exe.pdf"))
            self.assertFalse(allowed_file("run.exe"))
            self.assertTrue(allowed_file("coi.final.pdf"))
            self.assertTrue(allowed_file("COI FINAL.PDF"))
            self.assertTrue(allowed_file("file..pdf"))

    def test_project_upload_rejects_disallowed_extension(self):
        with self.app.app_context():
            project = Project(name="Project", user_id=self.user_id)
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login_session(self.user_id)
        token = self.csrf_token()
        response = self.client.post(
            f"/project/{project_id}/upload",
            data={
                "doc_type": "Contract",
                "csrf_token": token,
                "file": (BytesIO(b"<svg></svg>"), "bad.svg"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertEqual(Document.query.filter_by(project_id=project_id).count(), 0)

    def test_large_upload_returns_413(self):
        self.app.config["MAX_CONTENT_LENGTH"] = 128
        with self.app.app_context():
            project = Project(name="Project", user_id=self.user_id)
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login_session(self.user_id)
        token = self.csrf_token()
        response = self.client.post(
            f"/project/{project_id}/upload",
            data={
                "doc_type": "Contract",
                "csrf_token": token,
                "file": (BytesIO(b"x" * 2048), "large.pdf"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 413)
        self.assertNotIn(b"Traceback", response.data)

    def test_login_rate_limit(self):
        self.app.config["RATELIMIT_ENABLED"] = True
        self.app.config["LOGIN_RATE_LIMIT"] = "2 per minute"

        statuses = []
        for _ in range(3):
            token = self.csrf_token()
            response = self.client.post(
                "/login",
                data={
                    "email": "owner@example.com",
                    "password": "wrong-password",
                    "csrf_token": token,
                },
            )
            statuses.append(response.status_code)

        self.assertEqual(statuses, [200, 200, 429])
        self.assertNotIn(b"wrong-password", response.data)

    def test_login_rate_limit_window_resets(self):
        self.app.config["RATELIMIT_ENABLED"] = True
        self.app.config["LOGIN_RATE_LIMIT"] = "2 per minute"

        statuses = []
        with patch("app.security.time.monotonic", side_effect=[0, 1, 61]):
            for _ in range(3):
                token = self.csrf_token()
                response = self.client.post(
                    "/login",
                    data={
                        "email": "owner@example.com",
                        "password": "wrong-password",
                        "csrf_token": token,
                    },
                )
                statuses.append(response.status_code)

        self.assertEqual(statuses, [200, 200, 200])

    def test_login_rate_limit_isolated_by_client_ip(self):
        self.app.config["RATELIMIT_ENABLED"] = True
        self.app.config["LOGIN_RATE_LIMIT"] = "1 per minute"

        first_token = self.csrf_token()
        first = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "wrong-password",
                "csrf_token": first_token,
            },
            environ_overrides={"REMOTE_ADDR": "10.0.0.1"},
        )

        second_client = self.app.test_client()
        second_token = self.csrf_token(client=second_client)
        second = second_client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "wrong-password",
                "csrf_token": second_token,
            },
            environ_overrides={"REMOTE_ADDR": "10.0.0.2"},
        )

        blocked_token = self.csrf_token()
        blocked = self.client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "wrong-password",
                "csrf_token": blocked_token,
            },
            environ_overrides={"REMOTE_ADDR": "10.0.0.1"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(blocked.status_code, 429)

    def test_destructive_routes_do_not_allow_get(self):
        for path in [
            "/delete_project/1",
            "/delete_sub/1",
            "/delete_document/1",
            "/send_reminder/1",
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 405)

    def test_manual_reminder_requires_csrf_and_uses_post(self):
        with self.app.app_context():
            sub = Subcontractor(
                name="Reminder Sub",
                email="sub@example.com",
                user_id=self.user_id,
            )
            db.session.add(sub)
            db.session.commit()
            sub_id = sub.id

        self.login_session(self.user_id)

        missing = self.client.post(f"/send_reminder/{sub_id}")
        token = self.csrf_token()

        with patch(
            "app.routes.notifications.send_email_reminder",
            return_value=True,
        ) as send_mock:
            valid = self.client.post(
                f"/send_reminder/{sub_id}",
                data={"csrf_token": token},
            )

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(valid.status_code, 302)
        send_mock.assert_called_once()

    def test_delete_other_users_document_still_blocked(self):
        with self.app.app_context():
            other_sub = Subcontractor(name="Other", user_id=self.other_user_id)
            db.session.add(other_sub)
            db.session.flush()
            document = Document(
                filename="missing.pdf",
                original_name="missing.pdf",
                document_type="COI",
                sub_id=other_sub.id,
                uploaded_by=self.other_user_id,
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        self.login_session(self.user_id)
        token = self.csrf_token()
        response = self.client.post(
            f"/delete_document/{document_id}",
            data={"csrf_token": token},
        )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Document, document_id))


if __name__ == "__main__":
    unittest.main()
