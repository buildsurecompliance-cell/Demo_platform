import os
import uuid

from datetime import datetime, timedelta, date, timezone

import pytz
import requests

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash,
    send_from_directory, session
)

from flask_sqlalchemy import SQLAlchemy

from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
    UserMixin
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename

from dotenv import load_dotenv

from apscheduler.schedulers.background import BackgroundScheduler

from sqlalchemy.orm import joinedload
import re
# ==========================
# CONFIG
# ==========================

load_dotenv()

app = Flask(__name__)

# ==========================
# SECURITY
# ==========================

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY not set in environment variables")

# ==========================
# DATABASE
# ==========================

database_url = os.getenv("DATABASE_URL")

# corrige postgres:// para postgresql://
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ==========================
# FILE UPLOAD
# ==========================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, "uploads")

# limite de upload (10MB)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ==========================
# INIT
# ==========================

db = SQLAlchemy(app)
with app.app_context():
    db.create_all()

login_manager = LoginManager()

login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."

login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None

class User(UserMixin, db.Model):

    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)

    email = db.Column(
        db.String(120),
        unique=True,
        index=True,
        nullable=False
    )

    password_hash = db.Column(db.String(200), nullable=False)

    # controle de assinatura SaaS
    paid = db.Column(db.Boolean, default=False)

    # timezone do usuário
    timezone = db.Column(db.String(50), default="US/Eastern")

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # relationships
    projects = db.relationship(
        "Project",
        backref="owner",
        lazy=True,
        cascade="all, delete-orphan"
    )

    subs = db.relationship(
        "Subcontractor",
        backref="owner",
        lazy=True,
        cascade="all, delete-orphan"
    )

    # ==========================
    # PASSWORD METHODS
    # ==========================

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
#====================
# CLASS SUBCONTRACTOR
#====================
class Subcontractor(db.Model):

    __tablename__ = "subcontractor"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    # ==========================
    # PROJECT RELATIONSHIP
    # ==========================

    projects = db.relationship(
    "ProjectSubcontractor",
    back_populates="subcontractor",
    lazy="joined",
    cascade="all, delete-orphan"
    )   

    @property
    def linked_projects(self):
        return [link.project for link in self.projects]

    # ==========================
    # BASIC INFO
    # ==========================

    name = db.Column(
        db.String(150),
        nullable=False
    )

    email = db.Column(
        db.String(150),
        index=True
    )

    phone = db.Column(db.String(30))

    role = db.Column(db.String(100))

    timezone = db.Column(
        db.String(50),
        default="US/Eastern"
    )

    # ==========================
    # COMPLIANCE
    # ==========================

    coi_expiration = db.Column(
        db.Date,
        index=True
    )

    last_reminder_sent = db.Column(db.DateTime)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # ==========================
    # DOCUMENTS
    # ==========================

    documents = db.relationship(
        "Document",
        backref="sub",
        lazy="selectin",
        cascade="all, delete-orphan"
    )

    # ==========================
    # BUSINESS LOGIC
    # ==========================

    @property
    def days_left(self):

        if not self.coi_expiration:
            return None

        today = date.today()

        return (self.coi_expiration - today).days


    @property
    def computed_status(self):

        days = self.days_left

        if days is None:
            return "compliant"

        if days < 0:
            return "expired"

        if days <= 30:
            return "at_risk"

        return "compliant"
    
#=================
#class Document
#=================

class Document(db.Model):

    __tablename__ = "document"

    id = db.Column(db.Integer, primary_key=True)

    # ==========================
    # FILE INFO
    # ==========================

    filename = db.Column(
        db.String(255),
        nullable=False
    )

    original_name = db.Column(
        db.String(255)
    )

    document_type = db.Column(
        db.String(100),
        index=True
    )

    version = db.Column(
        db.Integer,
        default=1
    )

    # ==========================
    # RELATIONSHIPS
    # ==========================

    # document linked to subcontractor
    sub_id = db.Column(
        db.Integer,
        db.ForeignKey("subcontractor.id"),
        index=True
    )

    # document linked to project
    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        index=True
    )

    # who uploaded
    uploaded_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        index=True
    )

    uploader = db.relationship(
        "User",
        backref="uploaded_documents"
    )

    # ==========================
    # TIMESTAMPS
    # ==========================

    uploaded_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        index=True
    )

    # ==========================
    # HELPERS
    # ==========================

    @property
    def display_name(self):
        return self.original_name or self.filename

    def __repr__(self):
        return f"<Document {self.id} {self.document_type} v{self.version}>"
# ==========================
# PROJECT MODELS (NOVO)
# ==========================
class Project(db.Model):

    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(255), nullable=False)

    contract_value = db.Column(
        db.Float,
        default=0
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    # RELAÇÃO COM PROJECTSUBCONTRACTOR
    subs = db.relationship(
    "ProjectSubcontractor",
    back_populates="project",
    lazy="joined",
    cascade="all, delete-orphan"
    )

    # =========================
    # DAYS REMAINING
    # =========================
    @property
    def days_remaining(self):

        if not self.end_date:
            return None

        remaining = (self.end_date - date.today()).days

        return max(remaining, 0)

    # =========================
    # CONTRACT STATUS
    # =========================
    @property
    def contract_status(self):

        if not self.end_date:
            return "Unknown"

        if self.days_remaining == 0:
            return "Expired"

        if self.days_remaining <= 30:
            return "Expiring Soon"

        return "Active"

    # =========================
    # COMPLIANCE SCORE
    # =========================
    @property
    def compliance_score(self):

        if not self.subs:
            return 100

        total = 0
        compliant = 0

        for ps in self.subs:

            if not ps.subcontractor:
                continue

            total += 1

            if ps.subcontractor.computed_status == "compliant":
                compliant += 1

        if total == 0:
            return 100

        return int((compliant / total) * 100)

    # =========================
    # RISK LEVEL
    # =========================
    @property
    def risk_level(self):

        score = self.compliance_score

        if score == 100:
            return "Low"

        if score >= 70:
            return "Medium"

        return "High"

    # =========================
    # MOBILIZATION STATUS
    # =========================
    @property
    def mobilization_status(self):

        statuses = []

        for ps in self.subs:

            sub = ps.subcontractor

            if not sub:
                continue

            statuses.append(sub.computed_status)

        if "expired" in statuses:
            return "Blocked"

        if "at_risk" in statuses:
            return "Pending Compliance"

        return "Ready to Mobilize"
#=================
# CLASS PROJECTSUB
#=================
class ProjectSubcontractor(db.Model):

    __tablename__ = "project_subcontractor"

    id = db.Column(db.Integer, primary_key=True)

    project_id = db.Column(
        db.Integer,
        db.ForeignKey("project.id"),
        nullable=False,
        index=True
    )

    subcontractor_id = db.Column(
        db.Integer,
        db.ForeignKey("subcontractor.id"),
        nullable=False,
        index=True
    )

    approved_for_project = db.Column(
        db.Boolean,
        default=False
    )

    coverage_limit = db.Column(
        db.Float,
        default=1000000
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    project = db.relationship(
        "Project",
        back_populates="subs"
    )

    subcontractor = db.relationship(
        "Subcontractor",
        back_populates="projects"
    )

    __table_args__ = (
        db.UniqueConstraint(
            "project_id",
            "subcontractor_id",
            name="unique_project_sub"
        ),
    )
# ==========================
# LOGIN MANAGER
# ==========================

@login_manager.user_loader
def load_user(user_id):
    if not user_id:
        return None

    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None

# ==========================
# TEMPLATE FILTER
# ==========================

@app.template_filter("format_local_time")
def format_local_time(value):

    if not value:
        return ""

    # timezone do usuário ou padrão
    tz_name = getattr(current_user, "timezone", "US/Eastern")

    try:
        tz = pytz.timezone(tz_name)

        # garante que o datetime está em UTC
        if value.tzinfo is None:
            value = pytz.utc.localize(value)

        local_time = value.astimezone(tz)

        return local_time.strftime("%Y-%m-%d %H:%M")

    except Exception:
        return value.strftime("%Y-%m-%d %H:%M")
# ==========================
# EMAIL REMINDER
# ==========================

def send_email_reminder(to_email, subject, message):

    api_key = os.environ.get("RESEND_API_KEY")

    if not api_key:
        print("RESEND_API_KEY not configured")
        return False

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "BuildSure <onboarding@resend.dev>",
                "to": [to_email],
                "subject": subject,
                "html": f"<p>{message}</p>",
            },
            timeout=10
        )

        print("EMAIL STATUS:", response.status_code)
        print("EMAIL RESPONSE:", response.text)

        return response.status_code in (200, 202)

    except requests.exceptions.RequestException as e:
        print("EMAIL ERROR:", str(e))
        return False
# ==========================
# AUTO REMINDER
# ==========================
from datetime import date, datetime, timezone
from sqlalchemy.orm import joinedload

REMINDER_DAYS = {90, 60, 45, 30, 15, 7, 3, 1}

def check_and_send_auto_reminders_for_all_users():

    today = date.today()

    subs = (
        Subcontractor.query
        .options(joinedload(Subcontractor.owner))
        .filter(Subcontractor.coi_expiration.isnot(None))
        .filter(Subcontractor.email.isnot(None))
        .all()
    )

    reminders_sent = 0

    for sub in subs:

        expiration = sub.coi_expiration

        # garante que é date
        if isinstance(expiration, datetime):
            expiration = expiration.date()

        days_left = (expiration - today).days

        # não envia se já expirou
        if days_left < 0:
            continue

        if days_left not in REMINDER_DAYS:
            continue

        # evita duplicação no mesmo dia
        if sub.last_reminder_sent:
            if sub.last_reminder_sent.date() == today:
                continue

        subject = "COI Expiration Reminder"

        message = f"""
Hello {sub.name},

Your Certificate of Insurance will expire on {expiration}.

Please upload an updated COI to remain compliant.

Thank you,
BuildSure Compliance
"""

        sent = send_email_reminder(sub.email, subject, message)

        if sent:
            sub.last_reminder_sent = datetime.now(timezone.utc)
            reminders_sent += 1

            print(
                f"[REMINDER SENT] {sub.email} | {days_left} days left"
            )

    if reminders_sent > 0:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("DB ERROR:", e)

    print(f"Total reminders sent: {reminders_sent}")
# ==========================
# MOBILIZATION STATUS LOGIC
# ==========================

from datetime import date, datetime

def calculate_mobilization_status(project):

    today = date.today()

    if not project.subs:
        return "Not Cleared"

    has_pending = False

    for ps in project.subs:

        sub = ps.subcontractor

        # Subcontractor inválido
        if not sub:
            return "Not Cleared"

        expiration = sub.coi_expiration

        # COI inexistente
        if not expiration:
            return "Not Cleared"

        # garante tipo date
        if isinstance(expiration, datetime):
            expiration = expiration.date()

        # COI já expirado
        if expiration < today:
            return "Not Cleared"

        # COI expira antes do final do projeto
        if project.end_date and expiration < project.end_date:
            return "Not Cleared"

        # coverage mínimo do projeto
        required = getattr(project, "required_coverage", None)

        if required:
            coverage = ps.coverage_limit or 0

            if coverage < required:
                return "Not Cleared"

        days_left = (expiration - today).days

        if days_left <= 30:
            has_pending = True

    if has_pending:
        return "Pending Compliance"

    return "Ready to Mobilize"
# ==========================
# ROUTES
# ==========================

@app.route("/")
def home():

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    return render_template("landing.html")
# --------------------------
# SUBSCRIBE
# --------------------------

@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():

    if request.method == "POST":

        email = request.form.get("email", "").lower().strip()

        if not email:
            flash("Email is required.", "danger")
            return redirect(url_for("subscribe"))

        if not EMAIL_REGEX.match(email):
            flash("Invalid email address.", "danger")
            return redirect(url_for("subscribe"))

        # pagamento simulado
        flash(
            "Payment successful! Now create your account.",
            "success"
        )

        return redirect(url_for("register", email=email))

    email_prefill = request.args.get("email", "")

    return render_template(
        "subscribe.html",
        email_prefill=email_prefill
    )

# --------------------------
# REGISTER
# --------------------------
EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")

@app.route("/register", methods=["GET", "POST"])
def register():

    email_prefill = request.args.get("email", "").lower().strip()

    if request.method == "POST":

        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")

        if not email:
            flash("Email is required", "danger")
            return render_template("register.html", email_prefill=email)

        if not EMAIL_REGEX.match(email):
            flash("Invalid email address", "danger")
            return render_template("register.html", email_prefill=email)

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("Email already registered", "danger")
            return render_template("register.html", email_prefill=email)

        if len(password) < 8:
            flash("Password must be at least 8 characters", "danger")
            return render_template("register.html", email_prefill=email)

        try:
            new_user = User(email=email, paid=True)
            new_user.set_password(password)

            db.session.add(new_user)
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            print("REGISTER ERROR:", e)

            flash("Something went wrong. Please try again.", "danger")
            return render_template("register.html", email_prefill=email)

        flash("Account created successfully! You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", email_prefill=email_prefill)
# --------------------------
# LOGIN
# --------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template("login.html", email=email)

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):

            if not user.paid:
                flash(
                    "You need to subscribe before accessing the platform.",
                    "warning"
                )
                return redirect(url_for("subscribe"))

            login_user(user, remember=True)

            next_page = request.args.get("next")

            if next_page:
                return redirect(next_page)

            return redirect(url_for("dashboard"))

        flash("Invalid credentials", "danger")

    return render_template("login.html")

#==========
# LOGOUT
#==========


@app.route("/logout")
@login_required
def logout():

    logout_user()

    session.clear()

    flash("You have been logged out.", "info")

    return redirect(url_for("login"))

#===============
# DASHBOARD
#===============
@app.route("/dashboard")
@login_required
def dashboard():

    # =========================
    # FILTERS
    # =========================

    status_filter = request.args.get("status")
    search = request.args.get("search", "").strip()

    project_search = request.args.get("project_search", "").strip()
    contract_status = request.args.get("contract_status")
    risk_level = request.args.get("risk_level")

    # =========================
    # SUBCONTRACTORS
    # =========================

    query = Subcontractor.query.filter_by(
        user_id=current_user.id
    )

    if search:
        query = query.filter(
            Subcontractor.name.ilike(f"%{search}%")
        )

    subs = query.all()

    # KPI counters
    expired_count = 0
    at_risk_count = 0
    compliant_count = 0

    for sub in subs:

        status = sub.computed_status

        if status == "expired":
            expired_count += 1

        elif status == "at_risk":
            at_risk_count += 1

        else:
            compliant_count += 1

    # FILTER SUBS BY STATUS
    if status_filter:
        subs = [
            s for s in subs
            if s.computed_status == status_filter
        ]

    # =========================
    # RISK PRIORITY
    # =========================

    def risk_priority(sub):

        status = sub.computed_status
        days_left = sub.days_left

        if status == "expired":
            return (-2, 0)

        if status == "at_risk":
            return (-1, days_left or 0)

        return (0, 999)

    top_risk = sorted(subs, key=risk_priority)

    # =========================
    # PROJECTS
    # =========================

    projects_query = Project.query.filter_by(
        user_id=current_user.id
    )

    # SEARCH PROJECT
    if project_search:
        projects_query = projects_query.filter(
            Project.name.ilike(f"%{project_search}%")
        )

    projects = projects_query.order_by(Project.id.desc()).all()

    filtered_projects = []

    for project in projects:

        # CONTRACT STATUS FILTER
        if contract_status:

            days = project.days_remaining

            if contract_status == "active" and (days is None or days <= 30):
                continue

            if contract_status == "expiring" and (days is None or days > 30 or days <= 0):
                continue

            if contract_status == "expired" and (days is None or days > 0):
                continue

        # RISK LEVEL FILTER
        if risk_level and project.risk_level != risk_level:
            continue

        filtered_projects.append(project)

    projects = filtered_projects

    # =========================
    # PORTFOLIO METRICS
    # =========================

    total_portfolio = 0
    revenue_at_risk = 0

    for project in projects:

        contract_value = project.contract_value or 0
        total_portfolio += contract_value

        if project.mobilization_status != "Ready to Mobilize":
            revenue_at_risk += contract_value

    # =========================
    # TEMPLATE
    # =========================

    return render_template(
        "dashboard.html",
        subs=subs,
        top_risk=top_risk,
        projects=projects,
        expired_count=expired_count,
        at_risk_count=at_risk_count,
        compliant_count=compliant_count,
        total_portfolio=total_portfolio,
        revenue_at_risk=revenue_at_risk
    )
# ==========================
# VIEW SUB DOCUMENTS (SaaS)
# ==========================
@app.route("/sub/<int:sub_id>/documents")
@login_required
def view_sub_documents(sub_id):

    sub = Subcontractor.query.filter_by(
        id=sub_id,
        user_id=current_user.id
    ).first_or_404()

    documents = (
        Document.query
        .filter_by(sub_id=sub.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    return render_template(
        "view_sub_documents.html",
        sub=sub,
        documents=documents
    )

# ==========================
# MANUAL REMINDER
# ==========================
from datetime import datetime, timezone

@app.route("/send_reminder/<int:sub_id>", methods=["GET", "POST"])
@login_required
def send_reminder(sub_id):

    sub = Subcontractor.query.filter_by(
        id=sub_id,
        user_id=current_user.id
    ).first_or_404()

    if not sub.email:
        flash("Subcontractor does not have an email.", "danger")
        return redirect(url_for("dashboard"))

    subject = "COI Expiration Reminder"

    expiration = sub.coi_expiration

    message = f"""
Hello {sub.name},

This is a reminder that your Certificate of Insurance expires on {expiration}.

Please upload an updated COI to remain compliant.

Thank you,
BuildSure Compliance
"""

    sent = send_email_reminder(sub.email, subject, message)

    if sent:
        try:
            sub.last_reminder_sent = datetime.now(timezone.utc)
            db.session.commit()

            flash("Reminder sent successfully.", "success")

        except Exception as e:
            db.session.rollback()
            print("REMINDER DB ERROR:", e)
            flash("Reminder sent but failed to record it.", "warning")

    else:
        flash("Failed to send reminder.", "danger")

    return redirect(url_for("dashboard"))

#===============
# DOCS
#===============
@app.route("/document/<int:doc_id>")
@login_required
def view_document(doc_id):

    doc = db.session.get(Document, doc_id)

    if not doc:
        flash("Document not found.", "danger")
        return redirect(url_for("dashboard"))

    # documento sem vínculo
    if not doc.sub_id and not doc.project_id:
        flash("Unauthorized", "danger")
        return redirect(url_for("dashboard"))

    # valida subcontractor
    if doc.sub_id:
        sub = db.session.get(Subcontractor, doc.sub_id)

        if not sub or sub.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    # valida projeto
    if doc.project_id:
        project = db.session.get(Project, doc.project_id)

        if not project or project.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    filepath = os.path.join(
        app.config["UPLOAD_FOLDER"],
        doc.filename
    )

    if not os.path.exists(filepath):
        flash("File not found.", "danger")
        return redirect(url_for("dashboard"))

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        doc.filename,
        as_attachment=False
    )
# ==========================
# DELETE DOCUMENT
# ==========================
@app.route("/delete_document/<int:doc_id>", methods=["POST"])
@login_required
def delete_document(doc_id):

    doc = db.session.get(Document, doc_id)

    if not doc:
        flash("Document not found.", "danger")
        return redirect(url_for("dashboard"))

    # documento sem vínculo
    if not doc.sub_id and not doc.project_id:
        flash("Unauthorized", "danger")
        return redirect(url_for("dashboard"))

    # vinculado a subcontractor
    if doc.sub_id:
        sub = db.session.get(Subcontractor, doc.sub_id)

        if not sub or sub.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    # vinculado a project
    if doc.project_id:
        project = db.session.get(Project, doc.project_id)

        if not project or project.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    file_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        doc.filename
    )

    try:
        if os.path.exists(file_path):
            os.remove(file_path)

        db.session.delete(doc)
        db.session.commit()

        flash("Document deleted successfully.", "success")

    except Exception as e:
        db.session.rollback()
        print("DELETE DOCUMENT ERROR:", e)
        flash("Error deleting document.", "danger")

    return redirect(request.referrer or url_for("dashboard"))

# --------------------------
# ADD SUBCONTRACTOR
# --------------------------
def allowed_file(filename):
    return "." in filename and \
           filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/add_sub", methods=["GET", "POST"])
@login_required
def add_sub():

    projects = Project.query.filter_by(
        user_id=current_user.id
    ).all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        phone = request.form.get("phone")
        role = request.form.get("role")

        if not name:
            flash("Subcontractor name is required.", "danger")
            return redirect(url_for("add_sub"))

        coi_raw = request.form.get("coi_expiration")

        if coi_raw:
            try:
                coi_expiration = datetime.strptime(
                    coi_raw, "%Y-%m-%d"
                ).date()
            except ValueError:
                flash("Invalid date format.", "danger")
                return redirect(url_for("add_sub"))
        else:
            coi_expiration = None

        new_sub = Subcontractor(
            name=name,
            email=email,
            phone=phone,
            role=role,
            timezone=current_user.timezone,
            coi_expiration=coi_expiration,
            user_id=current_user.id
        )

        db.session.add(new_sub)
        db.session.flush()  # garante new_sub.id

        # ==========================
        # LINK SUB TO PROJECTS
        # ==========================

        project_ids = request.form.getlist("projects")

        for pid in set(project_ids):

            link = ProjectSubcontractor(
                project_id=int(pid),
                subcontractor_id=new_sub.id
            )

            db.session.add(link)

        # ==========================
        # DOCUMENT UPLOAD
        # ==========================

        files = request.files.getlist("documents")

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}", "danger")
                continue

            try:

                original_name = secure_filename(file.filename)

                doc_type = request.form.get("doc_type") or "Document"

                existing_doc = (
                    Document.query
                    .filter_by(
                        sub_id=new_sub.id,
                        document_type=doc_type
                    )
                    .order_by(Document.version.desc())
                    .first()
                )

                new_version = existing_doc.version + 1 if existing_doc else 1

                unique_name = f"{uuid.uuid4().hex}_{original_name}"

                path = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    unique_name
                )

                file.save(path)

                new_doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    document_type=doc_type,
                    version=new_version,
                    sub_id=new_sub.id,
                    uploaded_by=current_user.id
                )

                db.session.add(new_doc)

            except Exception as e:
                print("UPLOAD ERROR:", e)
                flash(f"Error uploading {file.filename}", "danger")

        db.session.commit()

        flash("Subcontractor added successfully!", "success")

        return redirect(url_for("dashboard"))

    return render_template(
        "add_sub.html",
        sub=None,
        projects=projects,
        selected_projects=[]
    )
#======================
# DOWNLOAD DOC
#======================
@app.route("/download_document/<int:doc_id>")
@login_required
def download_document(doc_id):

    doc = Document.query.get_or_404(doc_id)

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        doc.filename,
        as_attachment=True,
        download_name=doc.original_name
    )
    
# ==========================
# EDIT SUBCONTRACTOR
# ==========================
@app.route("/edit_sub/<int:id>", methods=["GET", "POST"])
@login_required
def edit_sub(id):

    sub = Subcontractor.query.filter_by(
        id=id,
        user_id=current_user.id
    ).first_or_404()

    # carregar projetos do usuário
    projects = Project.query.filter_by(
        user_id=current_user.id
    ).all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").lower().strip()
        phone = request.form.get("phone")
        role = request.form.get("role")

        if not name:
            flash("Subcontractor name is required.", "danger")
            return redirect(url_for("edit_sub", id=sub.id))

        sub.name = name
        sub.email = email
        sub.phone = phone
        sub.role = role
        sub.timezone = current_user.timezone

        expiration_raw = request.form.get("coi_expiration")

        if expiration_raw:
            try:
                sub.coi_expiration = datetime.strptime(
                    expiration_raw,
                    "%Y-%m-%d"
                ).date()
            except ValueError:
                flash("Invalid date format.", "danger")
                return redirect(url_for("edit_sub", id=sub.id))
        else:
            sub.coi_expiration = None

        # ==========================
        # UPDATE PROJECT LINKS
        # ==========================

        project_ids = request.form.getlist("projects")

        # remover vínculos antigos
        ProjectSubcontractor.query.filter_by(
            subcontractor_id=sub.id
        ).delete(synchronize_session=False)

        # criar novos vínculos
        for pid in project_ids:

            link = ProjectSubcontractor(
                project_id=int(pid),
                subcontractor_id=sub.id
            )

            db.session.add(link)

        try:
            db.session.commit()
            flash("Subcontractor updated successfully.", "success")

        except Exception as e:
            db.session.rollback()
            print("EDIT SUB ERROR:", e)
            flash("Error updating subcontractor.", "danger")

        return redirect(url_for("dashboard"))

    # ==========================
    # PROJECTS ALREADY LINKED
    # ==========================

    selected_projects = [
        link.project_id
        for link in ProjectSubcontractor.query.filter_by(
            subcontractor_id=sub.id
        ).all()
    ]

    return render_template(
        "edit_sub.html",
        sub=sub,
        projects=projects,
        selected_projects=selected_projects
    )
# ==========================
# DELETE SUBCONTRACTOR
# ==========================
@app.route("/delete_sub/<int:id>", methods=["POST"])
@login_required
def delete_sub(id):

    sub = Subcontractor.query.filter_by(
        id=id,
        user_id=current_user.id
    ).first_or_404()

    try:

        # apagar documentos físicos
        for doc in sub.documents:

            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                doc.filename
            )

            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print("FILE DELETE ERROR:", e)

        db.session.delete(sub)
        db.session.commit()

        flash("Subcontractor deleted successfully.", "success")

    except Exception as e:

        db.session.rollback()
        print("DELETE SUB ERROR:", e)

        flash("Error deleting subcontractor.", "danger")

    return redirect(url_for("dashboard"))
 
#================
# ADD PROJECT 
#================
@app.route("/add_project", methods=["GET", "POST"])
@login_required
def add_project():

    subs = Subcontractor.query.filter_by(
        user_id=current_user.id
    ).all()

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        value_raw = request.form.get("contract_value")

        start_raw = request.form.get("start_date")
        end_raw = request.form.get("end_date")

        if not name:
            flash("Project name is required.", "danger")
            return redirect(url_for("add_project"))

        # =========================
        # CONTRACT VALUE
        # =========================

        try:
            contract_value = float(value_raw) if value_raw else 0
        except ValueError:
            flash("Invalid contract value.", "danger")
            return redirect(url_for("add_project"))

        # =========================
        # START DATE
        # =========================

        start_date = None
        if start_raw:
            try:
                start_date = datetime.strptime(
                    start_raw,
                    "%Y-%m-%d"
                ).date()
            except ValueError:
                flash("Invalid start date.", "danger")
                return redirect(url_for("add_project"))

        # =========================
        # END DATE
        # =========================

        end_date = None
        if end_raw:
            try:
                end_date = datetime.strptime(
                    end_raw,
                    "%Y-%m-%d"
                ).date()
            except ValueError:
                flash("Invalid end date.", "danger")
                return redirect(url_for("add_project"))

        # VALIDATE DATE ORDER
        if start_date and end_date and end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for("add_project"))

        # =========================
        # CREATE PROJECT
        # =========================

        project = Project(
            name=name,
            contract_value=contract_value,
            user_id=current_user.id,
            start_date=start_date,
            end_date=end_date
        )

        db.session.add(project)
        db.session.flush()

        # =========================
        # DOCUMENT UPLOAD
        # =========================

        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

        files = request.files.getlist("documents")

        for file in files:

            if not file or file.filename == "":
                continue

            if not allowed_file(file.filename):
                flash(f"Invalid file type: {file.filename}", "danger")
                continue

            try:

                original_name = file.filename
                safe_name = secure_filename(original_name)

                unique_name = f"{uuid.uuid4().hex}_{safe_name}"

                filepath = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    unique_name
                )

                file.save(filepath)

                doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    document_type="Project Document",
                    project_id=project.id
                )

                db.session.add(doc)

            except Exception as e:
                print("UPLOAD ERROR:", e)
                flash(f"Error uploading {file.filename}", "danger")

        # =========================
        # COMMIT
        # =========================

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print("PROJECT CREATE ERROR:", e)
            flash("Error creating project.", "danger")
            return redirect(url_for("add_project"))

        flash("Project created successfully", "success")

        return redirect(url_for("dashboard"))

    return render_template(
        "add_project.html",
        subs=subs
    )
#===================
# EDIT PROJECT 
#===================
from datetime import datetime

@app.route("/edit_project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):

    project = Project.query.filter_by(
        id=project_id,
        user_id=current_user.id
    ).first_or_404()

    subs = Subcontractor.query.filter_by(
        user_id=current_user.id
    ).all()

    if request.method == "POST":

        # ==========================
        # PROJECT NAME
        # ==========================

        project.name = request.form.get("name", "").strip()

        if not project.name:
            flash("Project name is required.", "danger")
            return redirect(url_for("edit_project", project_id=project.id))

        # ==========================
        # CONTRACT VALUE
        # ==========================

        value_raw = request.form.get("contract_value")

        try:
            project.contract_value = float(value_raw) if value_raw else 0
        except ValueError:
            flash("Invalid contract value.", "danger")
            return redirect(url_for("edit_project", project_id=project.id))

        # ==========================
        # CONTRACT DATES
        # ==========================

        start_raw = request.form.get("start_date")
        end_raw = request.form.get("end_date")

        try:

            start_date = (
                datetime.strptime(start_raw, "%Y-%m-%d").date()
                if start_raw else None
            )

            end_date = (
                datetime.strptime(end_raw, "%Y-%m-%d").date()
                if end_raw else None
            )

        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(url_for("edit_project", project_id=project.id))

        # valida ordem das datas
        if start_date and end_date and end_date < start_date:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for("edit_project", project_id=project.id))

        project.start_date = start_date
        project.end_date = end_date

        # ==========================
        # SUBCONTRACTOR LINKS
        # ==========================

        selected_subs = request.form.getlist("subcontractors")

        current_links = ProjectSubcontractor.query.filter_by(
            project_id=project.id
        ).all()

        current_sub_ids = [str(link.subcontractor_id) for link in current_links]

        # remover subs
        for link in current_links:
            if str(link.subcontractor_id) not in selected_subs:
                db.session.delete(link)

        # adicionar novos subs
        for sub_id in selected_subs:

            if sub_id not in current_sub_ids:

                sub = Subcontractor.query.filter_by(
                    id=sub_id,
                    user_id=current_user.id
                ).first()

                if sub:

                    new_link = ProjectSubcontractor(
                        project_id=project.id,
                        subcontractor_id=sub.id,
                        coverage_limit=0
                    )

                    db.session.add(new_link)

        # ==========================
        # DOCUMENT UPLOAD
        # ==========================

        file = request.files.get("file")

        if file and file.filename != "":

            if allowed_file(file.filename):

                original_name = file.filename
                safe_name = secure_filename(original_name)

                unique_name = f"{uuid.uuid4().hex}_{safe_name}"

                path = os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    unique_name
                )

                file.save(path)

                doc_type = request.form.get("doc_type", "Document")

                last_doc = Document.query.filter_by(
                    project_id=project.id,
                    document_type=doc_type
                ).order_by(Document.version.desc()).first()

                version = last_doc.version + 1 if last_doc else 1

                new_doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    document_type=doc_type,
                    version=version,
                    project_id=project.id
                )

                db.session.add(new_doc)

        # ==========================
        # SAVE
        # ==========================

        try:
            db.session.commit()

        except Exception as e:

            db.session.rollback()
            print("PROJECT UPDATE ERROR:", e)

            flash("Error updating project.", "danger")

            return redirect(url_for("edit_project", project_id=project.id))

        flash("Project updated successfully!", "success")

        return redirect(url_for("view_project", project_id=project.id))

    return render_template(
        "edit_project.html",
        project=project,
        subs=subs
    )
#==================
# VIEW PROJECT
#==================
from collections import defaultdict
from sqlalchemy.orm import joinedload

@app.route("/project/<int:project_id>")
@login_required
def view_project(project_id):

    project = (
        Project.query
        .filter_by(
            id=project_id,
            user_id=current_user.id
        )
        .first_or_404()
    )

    # ==========================
    # SUBCONTRACTORS
    # ==========================

    links = (
        ProjectSubcontractor.query
        .options(joinedload(ProjectSubcontractor.subcontractor))
        .filter_by(project_id=project.id)
        .all()
    )

    # ==========================
    # DOCUMENTS
    # ==========================

    docs = (
        Document.query
        .filter_by(project_id=project.id)
        .order_by(
            Document.document_type,
            Document.version.desc()
        )
        .all()
    )

    # agrupar por tipo
    documents = defaultdict(list)

    for doc in docs:
        documents[doc.document_type].append(doc)

    return render_template(
        "view_project.html",
        project=project,
        links=links,
        documents=dict(documents)  # evita comportamento estranho no template
    )
#==================
# DELETE PROJECT
#==================

@app.route("/delete_project/<int:id>", methods=["POST"])
@login_required
def delete_project(id):

    project = Project.query.filter_by(
        id=id,
        user_id=current_user.id
    ).first_or_404()

    try:

        # =========================
        # DELETE DOCUMENT FILES
        # =========================

        documents = Document.query.filter_by(
            project_id=project.id
        ).all()

        for doc in documents:

            file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                doc.filename
            )

            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print("FILE DELETE ERROR:", e)

            db.session.delete(doc)

        # =========================
        # REMOVE SUB RELATIONS
        # =========================

        links = ProjectSubcontractor.query.filter_by(
            project_id=project.id
        ).all()

        for link in links:
            db.session.delete(link)

        # =========================
        # DELETE PROJECT
        # =========================

        db.session.delete(project)

        db.session.commit()

        flash("Project deleted successfully", "success")

    except Exception as e:

        db.session.rollback()
        print("DELETE PROJECT ERROR:", e)

        flash("Error deleting project.", "danger")

    return redirect(url_for("dashboard"))
#===============
# UP DOC PROJECT
#===============
@app.route("/project/<int:project_id>/upload", methods=["POST"])
@login_required
def upload_project_document(project_id):

    project = Project.query.filter_by(
        id=project_id,
        user_id=current_user.id
    ).first_or_404()

    file = request.files.get("file")

    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("view_project", project_id=project.id))

    if not allowed_file(file.filename):
        flash("Invalid file type.", "danger")
        return redirect(url_for("view_project", project_id=project.id))

    doc_type = request.form.get("doc_type") or "Document"

    original_name = secure_filename(file.filename)

    unique_name = f"{uuid.uuid4().hex}_{original_name}"

    upload_folder = os.path.join(
        app.config["UPLOAD_FOLDER"],
        f"project_{project.id}"
    )

    os.makedirs(upload_folder, exist_ok=True)

    file_path = os.path.join(upload_folder, unique_name)

    try:
        file.save(file_path)
    except Exception as e:
        print("Upload error:", e)
        flash("Error uploading file.", "danger")
        return redirect(url_for("view_project", project_id=project.id))

    last_doc = Document.query.filter_by(
        project_id=project.id,
        document_type=doc_type
    ).order_by(Document.version.desc()).first()

    version = last_doc.version + 1 if last_doc else 1

    new_doc = Document(
    filename=unique_name,
    original_name=original_name,
    document_type=doc_type,
    version=version,
    project_id=project.id
)

    db.session.add(new_doc)
    db.session.commit()

    flash("Document uploaded successfully!", "success")

    return redirect(url_for("view_project", project_id=project.id))

# ==========================
# APSCHEDULER - DAILY REMINDER
# ==========================

scheduler = BackgroundScheduler(
    timezone=pytz.timezone("America/Sao_Paulo")
)

def start_scheduler():

    if scheduler.running:
        return

    scheduler.add_job(
        func=check_and_send_auto_reminders_for_all_users,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_coi_reminder",
        replace_existing=True
    )

    scheduler.start()

# ==========================
# RUN
# ==========================
if __name__ == "__main__":

    with app.app_context():
        db.create_all()

    # evita iniciar 2 schedulers no modo debug
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)