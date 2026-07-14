import logging

from dataclasses import dataclass
from types import MappingProxyType

from app.extensions import db


STARTER = "STARTER"
PROFESSIONAL = "PROFESSIONAL"
ENTERPRISE = "ENTERPRISE"

PROJECT_LIMIT_REACHED = "PROJECT_LIMIT_REACHED"
SUBCONTRACTOR_LIMIT_REACHED = "SUBCONTRACTOR_LIMIT_REACHED"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanDefinition:
    key: str
    name: str
    max_projects: int | None
    max_subcontractors: int | None
    unlimited_users: bool = True


@dataclass(frozen=True)
class OrganizationUsage:
    project_count: int
    subcontractor_count: int


@dataclass(frozen=True)
class CapacityCheck:
    allowed: bool
    resource: str
    current: int
    limit: int | None
    plan_key: str
    reason_code: str | None = None
    message: str = ""


class PlanCapacityError(Exception):
    def __init__(self, check):
        super().__init__(check.message)
        self.check = check


_PLAN_DEFINITIONS = {
    STARTER: PlanDefinition(
        key=STARTER,
        name="Starter",
        max_projects=10,
        max_subcontractors=25,
    ),
    PROFESSIONAL: PlanDefinition(
        key=PROFESSIONAL,
        name="Professional",
        max_projects=50,
        max_subcontractors=300,
    ),
    ENTERPRISE: PlanDefinition(
        key=ENTERPRISE,
        name="Enterprise",
        max_projects=None,
        max_subcontractors=None,
    ),
}

PLAN_DEFINITIONS = MappingProxyType(_PLAN_DEFINITIONS)
PLAN_KEYS = tuple(PLAN_DEFINITIONS.keys())


def _require_organization(organization):
    if not organization or not getattr(organization, "id", None):
        raise ValueError("An active organization is required.")

    return organization


def validate_plan_key(plan_key):
    normalized = (plan_key or "").strip().upper()

    if normalized not in PLAN_DEFINITIONS:
        raise ValueError("Invalid organization plan.")

    return normalized


def get_organization_plan(organization):
    organization = _require_organization(organization)
    plan_key = validate_plan_key(
        organization.plan_key
    )
    return PLAN_DEFINITIONS[plan_key]


def get_organization_usage(organization):
    from app.models import Project, Subcontractor

    organization = _require_organization(organization)
    organization_id = organization.id

    project_count = (
        db.session.query(Project.id)
        .filter(Project.organization_id == organization_id)
        .count()
    )
    subcontractor_count = (
        db.session.query(Subcontractor.id)
        .filter(Subcontractor.organization_id == organization_id)
        .count()
    )

    return OrganizationUsage(
        project_count=project_count,
        subcontractor_count=subcontractor_count,
    )


def _capacity_check(plan, resource, current, limit):
    if limit is None:
        return CapacityCheck(
            allowed=True,
            resource=resource,
            current=current,
            limit=None,
            plan_key=plan.key,
        )

    if current < limit:
        return CapacityCheck(
            allowed=True,
            resource=resource,
            current=current,
            limit=limit,
            plan_key=plan.key,
        )

    plural_resource = (
        "projects"
        if resource == "project"
        else "subcontractors"
    )
    reason_code = (
        PROJECT_LIMIT_REACHED
        if resource == "project"
        else SUBCONTRACTOR_LIMIT_REACHED
    )

    return CapacityCheck(
        allowed=False,
        resource=resource,
        current=current,
        limit=limit,
        plan_key=plan.key,
        reason_code=reason_code,
        message=(
            f"Your {plan.name} plan allows up to "
            f"{limit} {plural_resource}."
        ),
    )


def can_create_project(organization):
    plan = get_organization_plan(organization)
    usage = get_organization_usage(organization)
    return _capacity_check(
        plan=plan,
        resource="project",
        current=usage.project_count,
        limit=plan.max_projects,
    )


def can_create_subcontractor(organization):
    plan = get_organization_plan(organization)
    usage = get_organization_usage(organization)
    return _capacity_check(
        plan=plan,
        resource="subcontractor",
        current=usage.subcontractor_count,
        limit=plan.max_subcontractors,
    )


def require_project_capacity(organization):
    check = can_create_project(organization)

    if not check.allowed:
        raise PlanCapacityError(check)

    return check


def require_subcontractor_capacity(organization):
    check = can_create_subcontractor(organization)

    if not check.allowed:
        raise PlanCapacityError(check)

    return check


def set_organization_plan(organization, plan_key):
    organization = _require_organization(organization)
    normalized = validate_plan_key(plan_key)
    previous = organization.plan_key
    organization.plan_key = normalized

    logger.info(
        "Organization plan updated organization_id=%s previous_plan=%s new_plan=%s",
        getattr(organization, "id", None),
        previous,
        normalized,
    )

    return organization
