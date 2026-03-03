from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timezone
import os
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import requests


# ==========================
# CONFIG
# ==========================
app = Flask(__name__)

# Segurança
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "dev-secret-key")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


# Email config segura
api_key = os.environ.get("RESEND_API_KEY")

# Upload folder
UPLOAD_FOLDER = os.path.join('static', 'docs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ==========================
# INIT
# ==========================
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ==========================
# MODELS
# ==========================
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    timezone = db.Column(db.String(50), default="US/Eastern")
    paid = db.Column(db.Boolean, default=False)
    subs = db.relationship('Subcontractor', backref='owner', lazy=True)

class Subcontractor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    role = db.Column(db.String(100))
    coi_expiration = db.Column(db.Date)
    last_reminder_sent = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="compliant")
    timezone = db.Column(db.String(50), default="US/Eastern")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    documents = db.relationship('Document', backref='sub', lazy=True)

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200))
    type = db.Column(db.String(50))
    sub_id = db.Column(db.Integer, db.ForeignKey('subcontractor.id'))

# ==========================
# LOGIN MANAGER
# ==========================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ==========================
# TEMPLATE FILTER
# ==========================
@app.template_filter('format_local_time')
def format_local_time(value, tz_name="US/Eastern"):
    if not value:
        return ""
    try:
        tz = pytz.timezone(tz_name)
        return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value.strftime("%Y-%m-%d %H:%M")

# ==========================
# EMAIL REMINDER
# ==========================

def send_email_reminder(to_email, subject, message):
    api_key = os.environ.get("RESEND_API_KEY")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": "onboarding@resend.dev",
            "to": to_email,
            "subject": subject,
            "html": f"<p>{message}</p>",
        },
    )

    print(response.status_code)
    print(response.text)

    return response.status_code == 200

# ==========================
# AUTO REMINDER
# ==========================

def check_and_send_auto_reminders_for_all_users():
    today = date.today()
    users = User.query.all()

    for user in users:
        subs = Subcontractor.query.filter_by(user_id=user.id).all()

        for sub in subs:
            if sub.coi_expiration:
                days_left = (sub.coi_expiration - today).days

                # Evita enviar 2 vezes no mesmo dia
                if sub.last_reminder_sent and sub.last_reminder_sent.date() == today:
                    continue

                if days_left in [45, 30, 15]:
                    subject = "COI Expiration Reminder"
                    message = f"Hello {sub.name}, your COI is expiring on {sub.coi_expiration}."

                    sent = send_email_reminder(sub.email, subject, message)

                    if sent:
                        sub.last_reminder_sent = datetime.now(timezone.utc)
                        db.session.commit()
                        print(f"Reminder sent to {sub.email} ({days_left} days left)")
# ==========================
# ROUTES
# ==========================

@app.route("/")
def home():
    return redirect(url_for("subscribe"))

# --------------------------
# SUBSCRIBE
# --------------------------

@app.route("/subscribe", methods=["GET","POST"])
def subscribe():
    if request.method == "POST":
        email = request.form.get("email")
        # Aqui você processaria o pagamento via Stripe/PayPal
        # Simulando pagamento OK:
        flash("Payment successful! Now please create your account.", "success")
        return redirect(url_for("register", email=email))
    return render_template("subscribe.html")

# --------------------------
# REGISTER
# --------------------------

@app.route("/register", methods=["GET","POST"])
def register():
    # Pega o email enviado do /subscribe
    email_prefill = request.args.get("email", "")

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        # Validação: email já registrado
        if User.query.filter_by(email=email).first():
            flash("Email already registered", "danger")
            return render_template("register.html", email_prefill=email)

        # Validação: senha mínima 8 caracteres
        if not password or len(password) < 8:
            flash("Password must be at least 8 characters", "danger")
            return render_template("register.html", email_prefill=email)

        # Criação do usuário pago
        hashed = generate_password_hash(password)
        new_user = User(email=email, password=hashed, paid=True)
        db.session.add(new_user)
        db.session.commit()

        flash("Account created successfully! You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", email_prefill=email_prefill)

# --------------------------
# LOGIN
# --------------------------

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            if not user.paid:
                flash("You need to subscribe before accessing the platform.", "warning")
                return redirect(url_for("subscribe"))
            login_user(user)
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


#=================================
#=== DASHBOARD 
#=================================

@app.route("/dashboard")
@login_required
def dashboard():
    subs = Subcontractor.query.filter_by(user_id=current_user.id).all()
    today = date.today()

    for sub in subs:
        if sub.coi_expiration:
            days_left = (sub.coi_expiration - today).days
            sub.days_left = days_left

            if days_left < 0:
                sub.computed_status = "expired"
            elif days_left <= 30:
                sub.computed_status = "at_risk"
            else:
                sub.computed_status = "compliant"
        else:
            sub.days_left = None
            sub.computed_status = "compliant"

    # mantém seu bloco de high risk
    top_risk = sorted(
        subs,
        key=lambda x: x.days_left if x.days_left is not None else 999
    )

    return render_template(
        "dashboard.html",
        subs=subs,
        top_risk=top_risk,
        today=today
    )
# --------------------------
# ADD / EDIT SUBCONTRACTOR
# --------------------------

@app.route("/add_sub", methods=["GET","POST"])
@login_required
def add_sub():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        role = request.form.get("role")
        coi_date = request.form.get("coi_expiration")
        coi_expiration = datetime.strptime(coi_date, "%Y-%m-%d").date()
        timezone = current_user.timezone

        new_sub = Subcontractor(
            name=name, email=email, phone=phone, role=role,
            coi_expiration=coi_expiration, timezone=timezone,
            user_id=current_user.id
        )
        db.session.add(new_sub)
        db.session.commit()

        files = request.files.getlist("documents")
        for file in files:
            if file.filename != "":
                filename = secure_filename(file.filename)
                path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(path)
                doc_type = request.form.get("doc_type") or "Document"
                new_doc = Document(filename=filename, type=doc_type, sub=new_sub)
                db.session.add(new_doc)
        db.session.commit()
        flash("Subcontractor added successfully!", "success")
        return redirect(url_for("dashboard"))
    return render_template("add_sub.html", sub=None)

@app.route("/edit_sub/<int:id>", methods=["GET","POST"])
@login_required
def edit_sub(id):
    sub = Subcontractor.query.filter_by(id=id, user_id=current_user.id).first()
    if not sub:
        flash("Not found", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        sub.name = request.form.get("name")
        sub.email = request.form.get("email")
        sub.phone = request.form.get("phone")
        sub.role = request.form.get("role")
        sub.coi_expiration = datetime.strptime(request.form.get("coi_expiration"), "%Y-%m-%d").date()
        sub.timezone = current_user.timezone

        files = request.files.getlist("documents")
        for file in files:
            if file.filename != "":
                filename = secure_filename(file.filename)
                path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(path)
                doc_type = request.form.get("doc_type") or "Document"
                new_doc = Document(filename=filename, type=doc_type, sub=sub)
                db.session.add(new_doc)
        db.session.commit()
        flash("Subcontractor updated!", "success")
        return redirect(url_for("dashboard"))
    return render_template("add_sub.html", sub=sub)

# --------------------------
# DELETE / SEND REMINDER
# --------------------------

@app.route("/delete_sub/<int:id>", methods=["POST"])
@login_required
def delete_sub(id):
    sub = Subcontractor.query.filter_by(id=id, user_id=current_user.id).first()

    if not sub:
        flash("Not found", "danger")
        return redirect(url_for("dashboard"))

    db.session.delete(sub)
    db.session.commit()

    flash("Deleted successfully!", "success")
    return redirect(url_for("dashboard"))

@app.route("/send_reminder/<int:id>", methods=["POST"])
@login_required
def send_reminder(id):
    sub = Subcontractor.query.filter_by(id=id, user_id=current_user.id).first()

    if not sub:
        flash("Not found", "danger")
        return redirect(url_for("dashboard"))

    subject = "COI Expiration Reminder"
    message = f"Hello {sub.name}, your COI is expiring on {sub.coi_expiration}."

    success = send_email_reminder(sub.email, subject, message)

    if success:

        sub.last_reminder_sent = datetime.now(timezone.utc)
        db.session.commit()
        flash("Reminder sent successfully!", "success")
    else:
        flash("Error sending email", "danger")

    return redirect(url_for("dashboard"))

# ==========================
# APSCHEDULER - DAILY REMINDER
# ==========================

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_and_send_auto_reminders_for_all_users, trigger="cron", hour=8, minute=0)
    scheduler.start()

# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        start_scheduler()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)