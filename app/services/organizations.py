import hashlib
import secrets

from datetime import datetime, timedelta, timezone

from flask import (
    abort,
    current_app,
    session,
)
from flask_login import current_user
from app.extensions import db
from app.models import (
    ORGANIZATION_ROLES,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    Subcontractor,
    User,
)
from app.services.subscription_service import (
    PROVIDER_INTERNAL,
    STATUS_ACTIVE,
    get_or_create_subscription,
)


MANAGE_MEMBER_ROLES = (
    ROLE_OWNER,
    ROLE_ADMIN,
)


def normalize_email(email):
    return (email or "").strip().lower()


def hash_invitation_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def default_organization_name(user):
    prefix = (user.email or "User").split("@", 1)[0].strip()
    if not prefix:
        prefix = "User"

    return f"{prefix}'s Organization"


def update_organization_name(organization, name):
    if not organization:
        raise ValueError("An active organization is required.")

    normalized_name = (name or "").strip()
    max_length = Organization.name.type.length

    if not normalized_name:
        raise ValueError("Company name is required.")

    if max_length and len(normalized_name) > max_length:
        raise ValueError(
            f"Company name must be {max_length} characters or fewer."
        )

    organization.name = normalized_name
    db.session.flush()
    return organization


def create_default_organization_for_user(user, role=ROLE_OWNER):
    organization = Organization(
        name=default_organization_name(user)
    )
    db.session.add(organization)
    db.session.flush()

    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
    )
    db.session.add(membership)
    db.session.flush()

    user.last_active_organization_id = organization.id

    get_or_create_subscription(
        organization,
        status=STATUS_ACTIVE,
        provider=PROVIDER_INTERNAL,
    )

    return organization


def get_user_memberships(user=None):
    user = user or current_user

    if not user or not getattr(user, "is_authenticated", False):
        return []

    return (
        OrganizationMembership.query
        .filter_by(user_id=user.id)
        .order_by(OrganizationMembership.id.asc())
        .all()
    )


def get_valid_invitation(token):
    token_hash = hash_invitation_token(token or "")
    invitation = (
        OrganizationInvitation.query
        .filter_by(token_hash=token_hash)
        .first()
    )

    if not invitation:
        return None

    if invitation.accepted_at:
        return None

    now = datetime.now(timezone.utc)

    if _as_aware_utc(invitation.expires_at) <= now:
        return None

    return invitation


def remember_active_organization(user, organization_id):
    if not user or not organization_id:
        return None

    membership = (
        OrganizationMembership.query
        .filter_by(
            organization_id=organization_id,
            user_id=user.id,
        )
        .first()
    )

    if not membership:
        return None

    user.last_active_organization_id = membership.organization_id
    session["organization_id"] = membership.organization_id
    session.modified = True
    return membership.organization


def resolve_active_organization(user=None):
    user = user or current_user

    if not user or not getattr(user, "is_authenticated", False):
        return None

    memberships = get_user_memberships(user)

    if not memberships:
        session.pop("organization_id", None)
        if getattr(user, "last_active_organization_id", None):
            user.last_active_organization_id = None
            db.session.flush()
        return None

    requested_id = session.get("organization_id")

    for membership in memberships:
        if membership.organization_id == requested_id:
            remember_active_organization(
                user,
                membership.organization_id,
            )
            return membership.organization

    last_active_id = getattr(user, "last_active_organization_id", None)

    for membership in memberships:
        if membership.organization_id == last_active_id:
            session["organization_id"] = membership.organization_id
            session.modified = True
            return membership.organization

    membership = memberships[0]
    user.last_active_organization_id = membership.organization_id
    session["organization_id"] = membership.organization_id
    session.modified = True
    db.session.flush()
    return membership.organization


def get_current_organization():
    if not current_user.is_authenticated:
        return None

    return resolve_active_organization(current_user)


def get_current_membership():
    organization = get_current_organization()

    if not organization:
        return None

    return (
        OrganizationMembership.query
        .filter_by(
            organization_id=organization.id,
            user_id=current_user.id,
        )
        .first()
    )


def user_belongs_to_organization(user_id, organization_id):
    return (
        OrganizationMembership.query
        .filter_by(
            user_id=user_id,
            organization_id=organization_id,
        )
        .first()
        is not None
    )


def current_user_role():
    membership = get_current_membership()
    return membership.role if membership else None


def can_manage_members(user=None, organization=None):
    user = user or current_user
    organization = organization or get_current_organization()

    if not organization or not user or not user.is_authenticated:
        return False

    membership = (
        OrganizationMembership.query
        .filter_by(
            organization_id=organization.id,
            user_id=user.id,
        )
        .first()
    )

    return bool(
        membership
        and membership.role in MANAGE_MEMBER_ROLES
    )


def require_organization_role(*roles):
    membership = get_current_membership()

    if not membership or membership.role not in roles:
        abort(403)

    return membership


def organization_member_user_ids(organization=None):
    organization = organization or get_current_organization()

    if not organization:
        return []

    rows = (
        OrganizationMembership.query
        .filter_by(organization_id=organization.id)
        .all()
    )

    return [
        membership.user_id
        for membership in rows
    ]


def project_scope_filter(model=Project, organization=None):
    organization = organization or get_current_organization()

    if not organization:
        return False

    return model.organization_id == organization.id


def subcontractor_scope_filter(model=Subcontractor, organization=None):
    return project_scope_filter(model, organization)


def scoped_project_query():
    return Project.query.filter(
        project_scope_filter(Project)
    )


def scoped_subcontractor_query():
    return Subcontractor.query.filter(
        subcontractor_scope_filter(Subcontractor)
    )


def set_domain_organization(entity, organization=None):
    organization = organization or get_current_organization()

    if not organization:
        raise ValueError("An active organization is required.")

    entity.organization_id = organization.id

    if hasattr(entity, "user_id") and not entity.user_id:
        entity.user_id = current_user.id


def validate_role(role):
    role = (role or "").strip().upper()

    if role not in ORGANIZATION_ROLES:
        return None

    return role


def create_invitation(email, role):
    organization = get_current_organization()
    require_organization_role(ROLE_OWNER, ROLE_ADMIN)

    normalized_email = normalize_email(email)
    role = validate_role(role)

    if not normalized_email or not role:
        raise ValueError("Invalid invitation.")

    pending = (
        OrganizationInvitation.query
        .filter_by(
            organization_id=organization.id,
            email=normalized_email,
            accepted_at=None,
        )
        .first()
    )

    if pending:
        raise ValueError("A pending invitation already exists.")

    token = secrets.token_urlsafe(32)
    invitation = OrganizationInvitation(
        organization_id=organization.id,
        email=normalized_email,
        role=role,
        token_hash=hash_invitation_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        invited_by=current_user.id,
    )

    db.session.add(invitation)
    db.session.flush()

    current_app.logger.info(
        "Organization invitation created organization_id=%s role=%s",
        organization.id,
        role,
    )

    return invitation, token


def _as_aware_utc(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def accept_invitation(token, user=None):
    user = user or current_user

    if not user or not user.is_authenticated:
        abort(403)

    invitation = get_valid_invitation(token)
    if not invitation:
        abort(404)

    if normalize_email(user.email) != invitation.email:
        abort(404)

    existing = (
        OrganizationMembership.query
        .filter_by(
            organization_id=invitation.organization_id,
            user_id=user.id,
        )
        .first()
    )

    if not existing:
        db.session.add(
            OrganizationMembership(
                organization_id=invitation.organization_id,
                user_id=user.id,
                role=invitation.role,
            )
        )

    invitation.accepted_at = datetime.now(timezone.utc)
    db.session.flush()
    remember_active_organization(
        user,
        invitation.organization_id,
    )

    return invitation


def list_members(organization=None):
    organization = organization or get_current_organization()

    if not organization:
        return []

    return (
        db.session.query(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .filter(OrganizationMembership.organization_id == organization.id)
        .order_by(OrganizationMembership.created_at.asc())
        .all()
    )


def pending_invitations(organization=None):
    organization = organization or get_current_organization()

    if not organization:
        return []

    return (
        OrganizationInvitation.query
        .filter_by(
            organization_id=organization.id,
            accepted_at=None,
        )
        .order_by(OrganizationInvitation.created_at.desc())
        .all()
    )
