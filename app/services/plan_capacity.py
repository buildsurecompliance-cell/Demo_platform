import logging

from dataclasses import dataclass
from types import MappingProxyType

from app.extensions import db


PLAN_STARTER = "STARTER"
PLAN_PROFESSIONAL = "PROFESSIONAL"
PLAN_ENTERPRISE = "ENTERPRISE"

STARTER = PLAN_STARTER
PROFESSIONAL = PLAN_PROFESSIONAL
ENTERPRISE = PLAN_ENTERPRISE

RESOURCE_PROJECT = "project"
RESOURCE_SUBCONTRACTOR = "subcontractor"

PROJECT_LIMIT_REACHED = "PROJECT_LIMIT_REACHED"
SUBCONTRACTOR_LIMIT_REACHED = "SUBCONTRACTOR_LIMIT_REACHED"
INVALID_PLAN = "INVALID_PLAN"
INVALID_RESOURCE = "INVALID_RESOURCE"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanDefinition:
    key: str
    name: str
    max_projects: int | None
    max_subcontractors: int | None


@dataclass(frozen=True)
class OrganizationUsage:
    project_count: int
    subcontractor_count: int


@dataclass(frozen=True)
class CapacityResult:
    allowed: bool
    resource: str
    current: int
    limit: int | None
    plan_key: str
    reason_code: str | None = None
    message: str | None = None


CapacityCheck = CapacityResult


@dataclass(frozen=True)
class PlanSelectionOption:
    plan_key: str
    display_name: str
    max_projects: int | None
    max_subcontractors: int | None
    unlimited_users: bool
    current_plan: str
    is_current: bool


class PlanCapacityError(Exception):
    def __init__(self, check):
        super().__init__(check.message)
        self.check = check
        self.resource = check.resource
        self.current = check.current
        self.limit = check.limit
        self.plan_key = check.plan_key
        self.reason_code = check.reason_code


_PLAN_DEFINITIONS = {
    PLAN_STARTER: PlanDefinition(
        key=PLAN_STARTER,
        name="Starter",
        max_projects=10,
        max_subcontractors=25,
    ),
    PLAN_PROFESSIONAL: PlanDefinition(
        key=PLAN_PROFESSIONAL,
        name="Professional",
        max_projects=50,
        max_subcontractors=300,
    ),
    PLAN_ENTERPRISE: PlanDefinition(
        key=PLAN_ENTERPRISE,
        name="Enterprise",
        max_projects=None,
        max_subcontractors=None,
    ),
}

PLAN_DEFINITIONS = MappingProxyType(_PLAN_DEFINITIONS)
PLAN_KEYS = tuple(PLAN_DEFINITIONS.keys())
LIMITED_RESOURCES = (
    RESOURCE_PROJECT,
    RESOURCE_SUBCONTRACTOR,
)


def _require_organization(organization):
    if not organization or not getattr(organization, "id", None):
        raise ValueError("An active organization is required.")

    return organization


def validate_plan_key(plan_key):
    normalized = (plan_key or "").strip().upper()

    if normalized not in PLAN_DEFINITIONS:
        raise ValueError("Invalid organization plan.")

    return normalized


def validate_resource(resource):
    normalized = (resource or "").strip().lower()

    if normalized not in LIMITED_RESOURCES:
        raise ValueError("Invalid capacity resource.")

    return normalized


def get_plan_definition(plan_key):
    return PLAN_DEFINITIONS[
        validate_plan_key(plan_key)
    ]


def get_organization_plan(organization):
    organization = _require_organization(organization)
    return get_plan_definition(
        organization.plan_key
    )


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


def get_plan_selection_options(organization):
    current_plan = get_organization_plan(organization)

    return [
        PlanSelectionOption(
            plan_key=plan.key,
            display_name=plan.name,
            max_projects=plan.max_projects,
            max_subcontractors=plan.max_subcontractors,
            unlimited_users=True,
            current_plan=current_plan.key,
            is_current=plan.key == current_plan.key,
        )
        for plan in PLAN_DEFINITIONS.values()
    ]


def get_plan_limit(organization, resource):
    plan = get_organization_plan(organization)
    resource = validate_resource(resource)

    if resource == RESOURCE_PROJECT:
        return plan.max_projects

    return plan.max_subcontractors


def _usage_for_resource(usage, resource):
    if resource == RESOURCE_PROJECT:
        return usage.project_count

    if resource == RESOURCE_SUBCONTRACTOR:
        return usage.subcontractor_count

    raise ValueError("Invalid capacity resource.")


def _limit_for_resource(plan, resource):
    if resource == RESOURCE_PROJECT:
        return plan.max_projects

    if resource == RESOURCE_SUBCONTRACTOR:
        return plan.max_subcontractors

    raise ValueError("Invalid capacity resource.")


def _resource_label(resource):
    if resource == RESOURCE_PROJECT:
        return "projects"

    if resource == RESOURCE_SUBCONTRACTOR:
        return "subcontractors"

    return resource


def _limit_reason_code(resource):
    if resource == RESOURCE_PROJECT:
        return PROJECT_LIMIT_REACHED

    if resource == RESOURCE_SUBCONTRACTOR:
        return SUBCONTRACTOR_LIMIT_REACHED

    return INVALID_RESOURCE


def _capacity_result(plan, resource, current, limit):
    if limit is None:
        return CapacityResult(
            allowed=True,
            resource=resource,
            current=current,
            limit=None,
            plan_key=plan.key,
        )

    if current < limit:
        return CapacityResult(
            allowed=True,
            resource=resource,
            current=current,
            limit=limit,
            plan_key=plan.key,
        )

    return CapacityResult(
        allowed=False,
        resource=resource,
        current=current,
        limit=limit,
        plan_key=plan.key,
        reason_code=_limit_reason_code(resource),
        message=(
            f"Your {plan.name} plan allows up to "
            f"{limit} {_resource_label(resource)}."
        ),
    )


def check_capacity(organization, resource):
    resource = validate_resource(resource)
    plan = get_organization_plan(organization)
    usage = get_organization_usage(organization)

    return _capacity_result(
        plan=plan,
        resource=resource,
        current=_usage_for_resource(usage, resource),
        limit=_limit_for_resource(plan, resource),
    )


def can_create_project(organization):
    return check_capacity(
        organization,
        RESOURCE_PROJECT,
    )


def can_create_subcontractor(organization):
    return check_capacity(
        organization,
        RESOURCE_SUBCONTRACTOR,
    )


def require_capacity(organization, resource):
    check = check_capacity(
        organization,
        resource,
    )

    if not check.allowed:
        raise PlanCapacityError(check)

    return check


def require_project_capacity(organization):
    return require_capacity(
        organization,
        RESOURCE_PROJECT,
    )


def require_subcontractor_capacity(organization):
    return require_capacity(
        organization,
        RESOURCE_SUBCONTRACTOR,
    )


def ensure_project_capacity(organization):
    return require_project_capacity(organization)


def ensure_subcontractor_capacity(organization):
    return require_subcontractor_capacity(organization)


def get_capacity(organization, resource):
    return check_capacity(
        organization,
        resource,
    )


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
