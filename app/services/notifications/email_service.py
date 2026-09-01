import os
import logging

import requests


logger = logging.getLogger(__name__)


# ==========================
# EMAIL REMINDER
# ==========================

def send_email_reminder(
    to_email,
    subject,
    message,
    html_message=None,
):

    api_key = os.environ.get(
        "RESEND_API_KEY"
    )

    if not api_key:
        logger.warning(
            "RESEND_API_KEY not configured"
        )
        return False

    try:
        payload = {
            "from": "BuildSure <onboarding@resend.dev>",
            "to": [to_email],
            "subject": subject,
            "html": html_message or f"<p>{message}</p>",
            "text": message,
        }

        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )

        logger.info(
            "Resend email request finished with status=%s",
            response.status_code,
        )

        if not response.ok:
            logger.warning(
                "Resend email request failed with status=%s",
                response.status_code,
            )

        return response.status_code in (
            200,
            202,
        )

    except requests.exceptions.RequestException:
        logger.exception(
            "Resend email request failed"
        )
        return False
