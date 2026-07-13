import logging
import re
import time
from functools import wraps
from urllib.parse import unquote, urljoin, urlparse

from flask import (
    abort,
    current_app,
    render_template,
    request,
    url_for,
)
from flask_wtf.csrf import CSRFError
from markupsafe import Markup
from werkzeug.exceptions import HTTPException

from app.extensions import db


logger = logging.getLogger(__name__)

_rate_limit_events = {}


def is_safe_redirect_target(target):
    if not target:
        return False

    decoded_target = unquote(target)

    if "\\" in decoded_target:
        return False

    if decoded_target.startswith("//"):
        return False

    host_url = request.host_url
    test_url = urlparse(urljoin(host_url, target))
    ref_url = urlparse(host_url)

    return (
        test_url.scheme in {"http", "https"}
        and test_url.netloc == ref_url.netloc
    )


def safe_redirect_target(target, fallback_endpoint="dashboard.dashboard"):
    if is_safe_redirect_target(target):
        return target

    return url_for(fallback_endpoint)


def register_security(app):
    app.context_processor(_security_context)
    app.after_request(_add_security_headers)
    _register_error_handlers(app)


def _security_context():
    return {
        "csrf_input": _csrf_input,
    }


def _csrf_input():
    from flask_wtf.csrf import generate_csrf

    return Markup(
        '<input type="hidden" name="csrf_token" '
        f'value="{generate_csrf()}">'
    )


def _add_security_headers(response):
    headers = current_app.config

    response.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff",
    )
    response.headers.setdefault(
        "X-Frame-Options",
        headers.get("X_FRAME_OPTIONS", "SAMEORIGIN"),
    )
    response.headers.setdefault(
        "Referrer-Policy",
        headers.get("REFERRER_POLICY", "strict-origin-when-cross-origin"),
    )
    response.headers.setdefault(
        "Permissions-Policy",
        headers.get(
            "PERMISSIONS_POLICY",
            "camera=(), microphone=(), geolocation=()",
        ),
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        headers.get("CONTENT_SECURITY_POLICY"),
    )

    return response


def _register_error_handlers(app):
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        logger.warning(
            "CSRF validation failed path=%s remote_addr=%s",
            request.path,
            request.remote_addr,
        )
        return render_template(
            "errors/403.html",
            title="Request blocked",
            message="Your session token is invalid or expired.",
        ), 403

    @app.errorhandler(403)
    def handle_forbidden(error):
        logger.warning(
            "Forbidden request path=%s remote_addr=%s",
            request.path,
            request.remote_addr,
        )
        return render_template(
            "errors/403.html",
            title="Access denied",
            message="You do not have permission to access this page.",
        ), 403

    @app.errorhandler(404)
    def handle_not_found(error):
        return render_template(
            "errors/404.html",
            title="Page not found",
            message="The page you requested could not be found.",
        ), 404

    @app.errorhandler(413)
    def handle_file_too_large(error):
        logger.warning(
            "Upload rejected for size path=%s remote_addr=%s",
            request.path,
            request.remote_addr,
        )
        return render_template(
            "errors/413.html",
            title="File too large",
            message="The uploaded file is larger than the allowed limit.",
        ), 413

    @app.errorhandler(429)
    def handle_rate_limited(error):
        logger.warning(
            "Rate limit exceeded path=%s remote_addr=%s",
            request.path,
            request.remote_addr,
        )
        return render_template(
            "errors/429.html",
            title="Too many attempts",
            message="Please wait before trying again.",
        ), 429

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            return error

        db.session.rollback()
        logger.error(
            "Unhandled server error path=%s remote_addr=%s error_type=%s",
            request.path,
            request.remote_addr,
            error.__class__.__name__,
        )
        return render_template(
            "errors/500.html",
            title="Something went wrong",
            message="We could not complete that request.",
        ), 500


def rate_limited(config_key):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if request.method != "POST":
                return view(*args, **kwargs)

            if not current_app.config.get("RATELIMIT_ENABLED", False):
                return view(*args, **kwargs)

            limit = parse_rate_limit(
                current_app.config.get(config_key)
            )

            if not limit:
                return view(*args, **kwargs)

            max_attempts, window_seconds = limit
            key = (
                config_key,
                request.remote_addr or "unknown",
            )
            now = time.monotonic()
            events = [
                event
                for event in _rate_limit_events.get(key, [])
                if now - event < window_seconds
            ]

            if len(events) >= max_attempts:
                _rate_limit_events[key] = events
                abort(429)

            events.append(now)
            _rate_limit_events[key] = events
            return view(*args, **kwargs)

        return wrapped

    return decorator


def parse_rate_limit(value):
    if not value:
        return None

    match = re.match(r"^\s*(\d+)\s+per\s+(minute|hour|day)\s*$", value)

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    seconds = {
        "minute": 60,
        "hour": 60 * 60,
        "day": 24 * 60 * 60,
    }[unit]

    return amount, seconds


def reset_rate_limits():
    _rate_limit_events.clear()
