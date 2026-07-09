from app import create_app

from app.extensions import scheduler

from app.services.notifications.reminder_service import (
    check_and_send_auto_reminders_for_all_users,
)


app = create_app()


if __name__ == "__main__":

    if not scheduler.running:

        scheduler.add_job(
            func=check_and_send_auto_reminders_for_all_users,
            trigger="interval",
            hours=24,
            id="auto_reminders",
            replace_existing=True,
        )

        scheduler.start()

    app.run(
        debug=True
    )