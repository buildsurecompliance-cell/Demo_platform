from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc)


def as_utc(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def mask_external_id(value):
    value = (value or "").strip()

    if not value:
        return ""

    if len(value) <= 8:
        return "****"

    prefix = value.split("_", 1)[0]
    suffix = value[-4:]

    if "_" in value:
        return f"{prefix}_****{suffix}"

    return f"****{suffix}"


def safe_reason_code(exc):
    reason_code = getattr(exc, "reason_code", None)
    if reason_code:
        return reason_code

    return exc.__class__.__name__
