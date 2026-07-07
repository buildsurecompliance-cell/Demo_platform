import pytz

from flask_login import current_user


def format_local_time(value):

    if not value:
        return ""

    tz_name = getattr(
        current_user,
        "timezone",
        None
    ) or "America/Sao_Paulo"

    try:
        user_tz = pytz.timezone(tz_name)

        if value.tzinfo is None:
            value = pytz.utc.localize(value)

        local_time = value.astimezone(user_tz)

        return local_time.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:
        return value.strftime(
            "%d/%m/%Y %H:%M"
        )


def register_template_filters(app):

    app.add_template_filter(
        format_local_time,
        "format_local_time"
    )