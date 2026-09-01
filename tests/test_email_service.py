import os
import unittest
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app.services.notifications.email_service import send_email_reminder


class EmailServiceTest(unittest.TestCase):

    def test_legacy_text_email_call_sends_text_and_default_html(self):
        with patch.dict(os.environ, {"RESEND_API_KEY": "resend-test"}):
            with patch(
                "app.services.notifications.email_service.requests.post",
            ) as post:
                post.return_value.ok = True
                post.return_value.status_code = 202

                sent = send_email_reminder(
                    "sub@example.com",
                    "Reminder subject",
                    "Plain message",
                )

        self.assertTrue(sent)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["to"], ["sub@example.com"])
        self.assertEqual(payload["subject"], "Reminder subject")
        self.assertEqual(payload["text"], "Plain message")
        self.assertEqual(payload["html"], "<p>Plain message</p>")

    def test_html_email_call_sends_html_and_plain_text(self):
        with patch.dict(os.environ, {"RESEND_API_KEY": "resend-test"}):
            with patch(
                "app.services.notifications.email_service.requests.post",
            ) as post:
                post.return_value.ok = True
                post.return_value.status_code = 200

                sent = send_email_reminder(
                    "sub@example.com",
                    "COI request",
                    "Plain fallback",
                    html_message="<strong>HTML request</strong>",
                )

        self.assertTrue(sent)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["text"], "Plain fallback")
        self.assertEqual(payload["html"], "<strong>HTML request</strong>")

    def test_provider_failure_returns_false(self):
        with patch.dict(os.environ, {"RESEND_API_KEY": "resend-test"}):
            with patch(
                "app.services.notifications.email_service.requests.post",
            ) as post:
                post.return_value.ok = False
                post.return_value.status_code = 500

                sent = send_email_reminder(
                    "sub@example.com",
                    "Subject",
                    "Message",
                )

        self.assertFalse(sent)

    def test_missing_api_key_returns_false_without_provider_call(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "app.services.notifications.email_service.requests.post",
            ) as post:
                sent = send_email_reminder(
                    "sub@example.com",
                    "Subject",
                    "Message",
                )

        self.assertFalse(sent)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
