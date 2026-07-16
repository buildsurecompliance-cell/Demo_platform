from .coi_prompt import COI_PROMPT
from .w9_prompt import W9_PROMPT
from .license_prompt import LICENSE_PROMPT
from .osha_prompt import OSHA_PROMPT
from .drug_test_prompt import DRUG_TEST_PROMPT
from .safety_training_prompt import SAFETY_TRAINING_PROMPT
from .background_check_prompt import BACKGROUND_CHECK_PROMPT
from .contract_prompt import CONTRACT_PROMPT


PROMPT_REGISTRY = {
    "contract": CONTRACT_PROMPT,
    "coi": COI_PROMPT,
    "w9": W9_PROMPT,
    "license": LICENSE_PROMPT,
    "osha": OSHA_PROMPT,
    "drug_test": DRUG_TEST_PROMPT,
    "safety_training": SAFETY_TRAINING_PROMPT,
    "background_check": BACKGROUND_CHECK_PROMPT,
}


def get_prompt_for_document(category):

    return PROMPT_REGISTRY.get(category)


__all__ = [
    "COI_PROMPT",
    "W9_PROMPT",
    "LICENSE_PROMPT",
    "OSHA_PROMPT",
    "DRUG_TEST_PROMPT",
    "SAFETY_TRAINING_PROMPT",
    "BACKGROUND_CHECK_PROMPT",
    "CONTRACT_PROMPT",
    "PROMPT_REGISTRY",
    "get_prompt_for_document",
]
