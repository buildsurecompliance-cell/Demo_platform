from datetime import date

from app.extensions import db


class Project(db.Model):

    __tablename__ = "project"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(255),
        nullable=False
    )

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

    # ==========================
    # RELATIONSHIPS
    # ==========================

    subs = db.relationship(
        "ProjectSubcontractor",
        back_populates="project",
        lazy="joined",
        cascade="all, delete-orphan"
    )

    # ==========================
    # DAYS REMAINING
    # ==========================

    @property
    def days_remaining(self):

        if not self.end_date:
            return None

        remaining = (
            self.end_date - date.today()
        ).days

        return max(remaining, 0)

    # ==========================
    # CONTRACT STATUS
    # ==========================

    @property
    def contract_status(self):

        if not self.end_date:
            return "Unknown"

        if self.days_remaining == 0:
            return "Expired"

        if self.days_remaining <= 30:
            return "Expiring Soon"

        return "Active"

    # ==========================
    # COMPLIANCE SCORE
    # ==========================

    @property
    def compliance_score(self):
        from app.services.readiness_service import (
            READY,
            calculate_readiness,
        )

        if not self.subs:
            return 100

        total = 0
        compliant = 0

        for ps in self.subs:

            if not ps.subcontractor:
                continue

            total += 1

            if calculate_readiness(ps)["status"] == READY:
                compliant += 1

        if total == 0:
            return 100

        return int(
            (compliant / total) * 100
        )

    # ==========================
    # RISK LEVEL
    # ==========================

    @property
    def risk_level(self):

        score = self.compliance_score

        if score == 100:
            return "Low"

        if score >= 70:
            return "Medium"

        return "High"

    # ==========================
    # MOBILIZATION STATUS
    # ==========================

    @property
    def mobilization_status(self):
        from app.services.readiness_service import (
            BLOCKED,
            PENDING,
            calculate_readiness,
        )

        if not self.subs:
            return "Ready to Mobilize"

        statuses = []

        for ps in self.subs:

            sub = ps.subcontractor

            if not sub:
                continue

            statuses.append(
                calculate_readiness(ps)["status"]
            )

        if BLOCKED in statuses:
            return "Blocked"

        if PENDING in statuses:
            return "Pending Compliance"

        return "Ready to Mobilize"

    # ==========================
    # DEBUG
    # ==========================

    def __repr__(self):

        return (
            f"<Project "
            f"{self.id} "
            f"{self.name}>"
        )
