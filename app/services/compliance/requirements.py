from app.services.compliance.models import ProjectRequirements


def get_default_project_requirements():

    return ProjectRequirements(
        general_liability=1000000,
        auto_liability=1000000,
        umbrella=0,
        workers_comp=True,
        additional_insured=True,
        waiver_of_subrogation=True,
        primary_non_contributory=False,
        coi_must_cover_project_duration=True,
    )


def get_project_requirements(project=None):

    # Temporary default.
    # Later this will read requirements from the project or compliance profile.
    return get_default_project_requirements()