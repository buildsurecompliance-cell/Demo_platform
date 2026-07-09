from .base_package import CompliancePackage
from .concrete_package import get_concrete_package
from .drywall_package import get_drywall_package
from .electrical_package import get_electrical_package
from .flooring_package import get_flooring_package
from .glazing_package import get_glazing_package
from .hvac_package import get_hvac_package
from .masonry_package import get_masonry_package
from .painting_package import get_painting_package
from .plumbing_package import get_plumbing_package
from .roofing_package import get_roofing_package
from .steel_package import get_steel_package
from .civil_package import get_civil_package
from .mechanical_package import get_mechanical_package


PACKAGE_REGISTRY = {
    "flooring": get_flooring_package,
    "drywall": get_drywall_package,
    "painting": get_painting_package,
    "roofing": get_roofing_package,
    "electrical": get_electrical_package,
    "plumbing": get_plumbing_package,
    "hvac": get_hvac_package,
    "concrete": get_concrete_package,
    "steel": get_steel_package,
    "glazing": get_glazing_package,
    "masonry": get_masonry_package,
    "civil": get_civil_package,
    "mechanical": get_mechanical_package,  
}


def get_package_for_trade(trade):

    if not trade:
        return get_flooring_package()

    key = trade.lower().strip()

    package_factory = PACKAGE_REGISTRY.get(key)

    if not package_factory:
        return get_flooring_package()

    return package_factory()


__all__ = [
    "CompliancePackage",
    "PACKAGE_REGISTRY",
    "get_package_for_trade",
    "get_flooring_package",
    "get_drywall_package",
    "get_painting_package",
    "get_roofing_package",
    "get_electrical_package",
    "get_plumbing_package",
    "get_hvac_package",
    "get_concrete_package",
    "get_steel_package",
    "get_glazing_package",
    "get_masonry_package",
    "get_civil_package",
    "get_mechanical_package",
]