from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timezone, timedelta
import os
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from flask_mail import Mail, Message
from pytz import timezone
import uuid
from flask import send_from_directory

# ==========================
# CONFIG
# ==========================
app = Flask(__name__)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx"}

def allowed_file(filename):
    return "." in filename and \
        filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

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

    subs = db.relationship(
        'Subcontractor',
        backref='owner',
        cascade="all, delete-orphan",
        lazy=True
    )

class Subcontractor(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False
    )

    name = db.Column(db.String(150))
    email = db.Column(db.String(150))
    phone = db.Column(db.String(30))
    role = db.Column(db.String(100))
    timezone = db.Column(db.String(50))
    coi_expiration = db.Column(db.Date)

    last_reminder_sent = db.Column(db.DateTime)

    documents = db.relationship(
        'Document',
        backref='sub',
        lazy=True,
        cascade="all, delete-orphan"
    )

    @property
    def days_left(self):
        if not self.coi_expiration:
            return None
        return (self.coi_expiration - date.today()).days

    @property
    def computed_status(self):

        days = self.days_left

        if days is None:
            return "compliant"

        if days < 0:
            return "expired"
        elif days <= 30:
            return "at_risk"
        else:
            return "compliant"
    
    documents = db.relationship(
    'Document',
    backref='sub',
    lazy=True,
    cascade="all, delete-orphan"
)
    from datetime import datetime
    
class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200))
    original_name = db.Column(db.String(200))  # NOVO
    type = db.Column(db.String(50))
    version = db.Column(db.Integer, default=1)  # NOVO
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

    sub_id = db.Column(db.Integer, db.ForeignKey('subcontractor.id'), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True)

# ==========================
# PROJECT MODELS (NOVO)
# ==========================

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    contract_value = db.Column(db.Float)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    required_coverage = db.Column(db.Float, default=1000000)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    subs = db.relationship('ProjectSubcontractor', backref='project', lazy=True)

    documents = db.relationship(
        'Document',
        backref='project',
        lazy=True,
        cascade="all, delete-orphan"
    )
    @property
    def mobilization_status(self):

        if not self.subs:
            return "Not Cleared"

        has_pending = False
        has_blocked = False

        for ps in self.subs:

            sub = ps.subcontractor

            if not sub:
                has_blocked = True
                continue

            if not sub.coi_expiration:
                has_blocked = True
                continue

            if self.end_date and sub.coi_expiration < self.end_date:
                has_blocked = True
                continue

            if ps.coverage_limit < self.required_coverage:
                has_blocked = True
                continue

            if sub.days_left is not None and sub.days_left <= 30:
                has_pending = True

        if has_blocked:
            return "Not Cleared"

        if has_pending:
            return "Pending Compliance"

        return "Ready to Mobilize"


class ProjectSubcontractor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'))
    subcontractor_id = db.Column(db.Integer, db.ForeignKey('subcontractor.id'))
    approved_for_project = db.Column(db.Boolean, default=False)

    subcontractor = db.relationship('Subcontractor')
    coverage_limit = db.Column(db.Float, default=1000000)


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
            "from": "buildsurecompliance@gmail.com",
            "to": [to_email],
            "subject": subject,
            "html": f"<p>{message}</p>",
        },
    )

    print("STATUS:", response.status_code)
    print("RESPONSE:", response.text)

    return response.status_code in [200, 202]

# ==========================
# AUTO REMINDER
# ==========================

def check_and_send_auto_reminders_for_all_users():
    today = date.today()
    users = User.query.all()

    for user in users:
        subs = Subcontractor.query.filter_by(user_id=user.id).all()

        updates_made = False

        for sub in subs:

            if not sub.coi_expiration or not sub.email:
                continue

            days_left = (sub.coi_expiration - today).days

            # evita enviar mais de 1x por dia
            if sub.last_reminder_sent and sub.last_reminder_sent.date() == today:
                continue

            if days_left in [45, 30, 15, 7, 3, 1]:

                subject = "COI Expiration Reminder"

                message = f"""
Hello {sub.name},

Your Certificate of Insurance will expire on {sub.coi_expiration}.

Please upload an updated COI to remain compliant.

Thank you.
"""

                sent = send_email_reminder(sub.email, subject, message)

                if sent:
                    sub.last_reminder_sent = datetime.now(timezone.utc)
                    updates_made = True
                    print(f"Reminder sent to {sub.email} ({days_left} days left)")

        if updates_made:
            db.session.commit()

# ==========================
# MOBILIZATION STATUS LOGIC
# ==========================

def calculate_mobilization_status(project):
    today = date.today()

    if not project.subs:
        return "Not Cleared"

    has_pending = False
    has_blocked = False

    for ps in project.subs:

        sub = ps.subcontractor

        if not sub:
            has_blocked = True
            continue

        if not sub.coi_expiration:
            has_blocked = True
            continue

        if project.end_date and sub.coi_expiration < project.end_date:
            has_blocked = True
            continue

        if ps.coverage_limit < project.required_coverage:
            has_blocked = True
            continue

        days_left = (sub.coi_expiration - date.today()).days

        if days_left <= 30:
            has_pending = True

    if has_blocked:
        return "Not Cleared"

    if has_pending:
        return "Pending Compliance"

    return "Ready to Mobilize"
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

#===============
# DASHBOARD
#===============
@app.route("/dashboard")
@login_required
def dashboard():

    status_filter = request.args.get("status")
    search = request.args.get("search")

    # SUBCONTRACTORS
    query = Subcontractor.query.filter_by(
        user_id=current_user.id
    )

    if search:
        query = query.filter(Subcontractor.name.ilike(f"%{search}%"))

    subs = query.all()

    if status_filter:
        subs = [s for s in subs if s.computed_status == status_filter]

    def risk_priority(s):
        if s.computed_status == "expired":
            return -999
        if s.computed_status == "at_risk":
            return s.days_left or 0
        return 999

    top_risk = sorted(subs, key=risk_priority)

    # PROJECTS
    projects = Project.query.filter_by(
        user_id=current_user.id
    ).all()

    total_portfolio = 0
    revenue_at_risk = 0

    for project in projects:

        contract_value = project.contract_value or 0
        total_portfolio += contract_value

        if project.mobilization_status != "Ready to Mobilize":
            revenue_at_risk += contract_value

    return render_template(
        "dashboard.html",
        subs=subs,
        top_risk=top_risk,
        projects=projects,
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

    documents = Document.query.filter_by(
        sub_id=sub.id
    ).order_by(Document.upload_date.desc()).all()

    return render_template(
        "view_sub_documents.html",
        sub=sub,
        documents=documents
    )
#===============
# DOCS
#===============
@app.route("/document/<int:doc_id>")
@login_required
def view_document(doc_id):

    doc = Document.query.get_or_404(doc_id)

    # 🔒 Documento vinculado a Subcontractor
    if doc.sub_id:
        sub = Subcontractor.query.get(doc.sub_id)
        if not sub or sub.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    # 🔒 Documento vinculado a Project
    if doc.project_id:
        project = Project.query.get(doc.project_id)
        if not project or project.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        doc.filename
    )
# ==========================
# DELETE DOCUMENT
# ==========================
@app.route("/delete_document/<int:doc_id>", methods=["POST"])
@login_required
def delete_document(doc_id):

    doc = Document.query.get_or_404(doc_id)

    if doc.sub_id:
        sub = Subcontractor.query.get(doc.sub_id)
        if sub.user_id != current_user.id:
            flash("Unauthorized", "danger")
            return redirect(url_for("dashboard"))

    # remove arquivo físico
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], doc.filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(doc)
    db.session.commit()

    flash("Document deleted successfully.", "success")
    return redirect(request.referrer)

# --------------------------
# ADD SUBCONTRACTOR
# --------------------------
@app.route("/add_sub", methods=["GET", "POST"])
@login_required
def add_sub():

    if request.method == "POST":

        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        role = request.form.get("role")
        timezone = current_user.timezone

        coi_raw = request.form.get("coi_expiration")

        if coi_raw:
            try:
                coi_expiration = datetime.strptime(
                    coi_raw,
                    "%Y-%m-%d"
                ).date()
            except ValueError:
                flash("Invalid date format.", "danger")
                return redirect(url_for("add_sub"))
        else:
            coi_expiration = None

        # Criar Sub
        new_sub = Subcontractor(
            name=name,
            email=email,
            phone=phone,
            role=role,
            coi_expiration=coi_expiration,
            timezone=timezone,
            user_id=current_user.id
        )

        db.session.add(new_sub)
        db.session.flush()

        # 🔥 TUDO AQUI DENTRO DO POST
        files = request.files.getlist("documents")

        for file in files:

            if file and file.filename != "":

                doc_type = request.form.get("doc_type") or "Document"
                original_name = secure_filename(file.filename)

                existing_doc = Document.query.filter_by(
                    sub_id=new_sub.id
                ).order_by(Document.version.desc()).first()

                new_version = 1
                if existing_doc:
                    new_version = existing_doc.version + 1

                unique_name = f"{uuid.uuid4().hex}_{original_name}"
                path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                file.save(path)

                new_doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    type=doc_type,
                    version=new_version,
                    sub_id=new_sub.id
                )

                db.session.add(new_doc)

        db.session.commit()
        flash("Subcontractor added successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_sub.html", sub=None)
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

    if request.method == "POST":

        # 🔹 Atualiza dados básicos
        sub.name = request.form.get("name")
        sub.email = request.form.get("email")
        sub.phone = request.form.get("phone")
        sub.role = request.form.get("role")
        sub.timezone = current_user.timezone

        # 🔹 Atualiza COI expiration
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

        # 🔥 Upload com versionamento automático
        files = request.files.getlist("documents")

        for file in files:

            if file and file.filename != "":

                doc_type = request.form.get("doc_type") or "Document"
                original_name = secure_filename(file.filename)

                # pega última versão daquele tipo
                existing_doc = Document.query.filter_by(
                    sub_id=sub.id,
                    type=doc_type
                ).order_by(Document.version.desc()).first()

                new_version = 1
                if existing_doc:
                    new_version = existing_doc.version + 1

                unique_name = f"{uuid.uuid4().hex}_{original_name}"
                path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                file.save(path)

                new_doc = Document(
                    filename=unique_name,
                    original_name=original_name,
                    type=doc_type,
                    version=new_version,
                    sub_id=sub.id
                )

                db.session.add(new_doc)

        db.session.commit()

        flash("Subcontractor updated successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_sub.html", sub=sub)
# ------------
# DELETE 
# ------------

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

#================
# SEND REMINDER
#================
@app.route("/send_reminder/<int:sub_id>")
@login_required
def send_reminder(sub_id):

    sub = Subcontractor.query.get_or_404(sub_id)

    # 🔒 Segurança: garante que o sub pertence ao usuário logado
    if sub.user_id != current_user.id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))

    # ❗ Verifica email
    if not sub.email:
        flash("Subcontractor has no email registered.", "warning")
        return redirect(url_for("dashboard"))

    # ❗ Verifica data
    if not sub.coi_expiration:
        flash("No COI expiration date found.", "warning")
        return redirect(url_for("dashboard"))

    days_left = (sub.coi_expiration - date.today()).days

    # ❗ Só envia se faltar 30 dias ou menos
    if days_left > 30:
        flash("COI is not close to expiration.", "info")
        return redirect(url_for("dashboard"))

    subject = "Insurance Expiration Reminder"

    body = f"""
Hello {sub.name},

This is a reminder that your Certificate of Insurance
will expire on {sub.coi_expiration.strftime('%m/%d/%Y')}.

Please upload an updated COI as soon as possible.

Thank you.
"""

    try:
        
        send_email_reminder(sub.email, subject, body)

        flash("Reminder sent successfully.", "success")

    except Exception as e:
        print("Email error:", e)
        flash("Error sending email.", "danger")

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

        name = request.form["name"]
        contract_value = request.form.get("contract_value") or 0
        selected_subs = request.form.getlist("subcontractors")

        new_project = Project(
            name=name,
            contract_value=float(contract_value),
            user_id=current_user.id
        )

        db.session.add(new_project)
        db.session.commit()

        # 🔗 Criar vínculos com subs
        for sub_id in selected_subs:

            link = ProjectSubcontractor(
                project_id=new_project.id,
                subcontractor_id=int(sub_id),
                coverage_limit=0  # valor inicial padrão
            )

            db.session.add(link)

        db.session.commit()

        return redirect(url_for("dashboard"))

    return render_template("add_project.html", subs=subs)
#===================
# EDIT PROJECT 
#===================
@app.route("/edit_project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):

    project = Project.query.get_or_404(project_id)

    if request.method == "POST":

        project.name = request.form.get("name")
        project.contract_value = float(request.form.get("contract_value") or 0)

        db.session.commit()

        return redirect(url_for("dashboard"))

    return render_template("edit_project.html", project=project)

#==================
# VIEW PROJECT
#==================
@app.route("/project/<int:project_id>")
@login_required
def view_project(project_id):

    project = Project.query.filter_by(
        id=project_id,
        user_id=current_user.id
    ).first_or_404()

    return render_template("view_project.html", project=project)

#===============
# DOC PROJECT
#===============
@app.route("/project/<int:project_id>/upload", methods=["POST"])
@login_required
def upload_project_document(project_id):

    project = Project.query.filter_by(
        id=project_id,
        user_id=current_user.id
    ).first_or_404()

    if "file" not in request.files:
        flash("No file selected", "danger")
        return redirect(request.referrer)

    file = request.files["file"]

    if file.filename == "":
        flash("No file selected", "danger")
        return redirect(request.referrer)

    if not allowed_file(file.filename):
        flash("Invalid file type", "danger")
        return redirect(request.referrer)

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"

    upload_folder = app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)

    upload_path = os.path.join(upload_folder, unique_name)
    file.save(upload_path)

    new_doc = Document(
        filename=unique_name,
        type="Project Document",
        project_id=project.id
    )

    db.session.add(new_doc)
    db.session.commit()

    flash("Document uploaded successfully!", "success")
    return redirect(url_for("dashboard"))

# ==========================
# APSCHEDULER - DAILY REMINDER
# ==========================

scheduler = BackgroundScheduler(
    timezone=timezone("America/Sao_Paulo")
)

def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            check_and_send_auto_reminders_for_all_users,
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

if __name__ == "__main__" and not os.environ.get("WERKZEUG_RUN_MAIN"):

    with app.app_context():
        db.create_all()
        start_scheduler()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)