import re

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

from flask_login import (
    current_user,
    login_required,
    login_user,
    logout_user,
)

from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import User

auth_bp = Blueprint(
    "auth",
    __name__,
)

EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")


# ==========================
# HOME
# ==========================

@auth_bp.route("/")
def home():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.dashboard"))

    return redirect(url_for("auth.login"))


# ==========================
# SUBSCRIBE
# ==========================

@auth_bp.route(
    "/subscribe",
    methods=["GET", "POST"]
)
def subscribe():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).lower().strip()

        if not email:
            flash(
                "Email is required.",
                "danger"
            )
            return redirect(
                url_for("auth.subscribe")
            )

        if not EMAIL_REGEX.match(email):
            flash(
                "Invalid email address.",
                "danger"
            )
            return redirect(
                url_for("auth.subscribe")
            )

        # Temporary simulated payment
        flash(
            "Payment successful! Now create your account.",
            "success"
        )

        return redirect(
            url_for(
                "auth.register",
                email=email
            )
        )

    email_prefill = request.args.get(
        "email",
        ""
    )

    return render_template(
        "subscribe.html",
        email_prefill=email_prefill
    )


# ==========================
# REGISTER
# ==========================

@auth_bp.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    email_prefill = request.args.get(
        "email",
        ""
    ).lower().strip()

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).lower().strip()

        password = request.form.get(
            "password",
            ""
        )

        if not email:

            flash(
                "Email is required",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email
            )

        if not EMAIL_REGEX.match(email):

            flash(
                "Invalid email address",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email
            )

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:

            flash(
                "Email already registered",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email
            )

        if len(password) < 8:

            flash(
                "Password must be at least 8 characters",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email
            )

        try:

            new_user = User(
                email=email,
                paid=True
            )

            new_user.set_password(password)

            db.session.add(new_user)

            db.session.commit()

        except Exception as e:

            db.session.rollback()

            print(
                "REGISTER ERROR:",
                e
            )

            flash(
                "Something went wrong. Please try again.",
                "danger"
            )

            return render_template(
                "register.html",
                email_prefill=email
            )

        flash(
            "Account created successfully! You can now log in.",
            "success"
        )

        return redirect(
            url_for("auth.login")
        )

    return render_template(
        "register.html",
        email_prefill=email_prefill
    )


# ==========================
# LOGIN
# ==========================

@auth_bp.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).lower().strip()

        password = request.form.get(
            "password",
            ""
        )

        if not email or not password:

            flash(
                "Email and password are required.",
                "danger"
            )

            return render_template(
                "login.html",
                email=email
            )

        user = User.query.filter_by(
            email=email
        ).first()

        if user and check_password_hash(
            user.password_hash,
            password
        ):

            if not user.paid:

                flash(
                    "You need to subscribe before accessing the platform.",
                    "warning"
                )

                return redirect(
                    url_for("auth.subscribe")
                )

            login_user(
                user,
                remember=False
            )

            next_page = request.args.get(
                "next"
            )

            if next_page:
                return redirect(next_page)

            return redirect(
                url_for("dashboard.dashboard")
            )

        flash(
            "Invalid credentials",
            "danger"
        )

    return render_template(
        "login.html"
    )


# ==========================
# LOGOUT
# ==========================

@auth_bp.route("/logout")
@login_required
def logout():

    logout_user()

    session.clear()
    session.modified = True

    flash(
        "You have been logged out.",
        "info"
    )

    return redirect(
        url_for("auth.login")
    )