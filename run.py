import os

from app import create_app

from app.extensions import scheduler

from app.services.notifications.reminder_service import (
    check_and_send_auto_reminders_for_all_users,
)


app = create_app()


def _scheduler_enabled():
    return os.getenv(
        "SCHEDULER_ENABLED",
        "false",
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def start_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        func=check_and_send_auto_reminders_for_all_users,
        trigger="interval",
        hours=24,
        id="auto_reminders",
        replace_existing=True,
    )

    scheduler.start()


if __name__ == "__main__":

    if _scheduler_enabled():
        start_scheduler()

    app.run(
        debug=app.config["DEBUG"]
    )
