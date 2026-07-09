from .base_profile import ComplianceProfile
from .commercial_profile import get_commercial_profile
from .data_center_profile import get_data_center_profile


PROFILE_REGISTRY = {
    "commercial": get_commercial_profile,
    "commercial building": get_commercial_profile,
    "data_center": get_data_center_profile,
    "data center": get_data_center_profile,
}


def get_profile(profile_name=None):

    if not profile_name:
        return get_commercial_profile()

    key = profile_name.lower().strip()

    profile_factory = PROFILE_REGISTRY.get(key)

    if not profile_factory:
        return get_commercial_profile()

    return profile_factory()


__all__ = [
    "ComplianceProfile",
    "PROFILE_REGISTRY",
    "get_profile",
    "get_commercial_profile",
    "get_data_center_profile",
]