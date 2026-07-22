import json
from pathlib import Path

import click
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.demo_company_generator.generator import (
    generate_demo_company_package,
)
from app.services.demo_project_generator.project_document_builder import (
    build_project_documents,
)
from app.services.demo_project_generator.project_factory import (
    list_demo_projects,
    project_to_json,
)
from app.services.documents.storage import save_document_file


SUBCONTRACTOR_PRESETS = (
    ("apex-concrete", "ready"),
    ("titan-electrical", "ready"),
    ("precision-plumbing", "ready"),
    ("peak-mechanical", "ready"),
    ("elite-flooring", "ready"),
    ("vertex-glass", "ready"),
    ("guardian-fire", "pending"),
    ("blue-sky-roofing", "pending"),
    ("ironworks-steel", "blocked"),
    ("summit-drywall", "blocked"),
)

SUBCONTRACTOR_NAMES = {
    "apex-concrete": "Apex Concrete LLC",
    "titan-electrical": "Titan Electrical LLC",
    "precision-plumbing": "Precision Plumbing LLC",
    "peak-mechanical": "Peak Mechanical LLC",
    "elite-flooring": "Elite Flooring LLC",
    "vertex-glass": "Vertex Glass LLC",
    "guardian-fire": "Guardian Fire LLC",
    "blue-sky-roofing": "Blue Sky Roofing LLC",
    "ironworks-steel": "IronWorks Steel LLC",
    "summit-drywall": "Summit Drywall LLC",
}

SUBCONTRACTOR_TRADES = {
    "apex-concrete": "Concrete Contractor",
    "titan-electrical": "Electrical Contractor",
    "precision-plumbing": "Plumbing Contractor",
    "peak-mechanical": "HVAC / Mechanical Contractor",
    "elite-flooring": "Flooring Contractor",
    "vertex-glass": "Glass & Glazing Contractor",
    "guardian-fire": "Fire Protection Contractor",
    "blue-sky-roofing": "Roofing Contractor",
    "ironworks-steel": "Structural Steel Contractor",
    "summit-drywall": "Drywall Contractor",
}


def generate_demo_project_package(
    *,
    preset="summit-distribution-center",
    scenario="ready",
    output="instance/demo_projects",
    seed=123,
    populate=False,
    create_records=False,
    unique_subcontractors=False,
):
    project = _find_project(preset, seed)
    base_dir = Path(output) / project.key
    project_dir = base_dir / "project_documents"
    project_dir.mkdir(parents=True, exist_ok=True)

    documents = build_project_documents(
        project,
        project_dir,
    )
    subcontractors = []

    if populate:
        subs_dir = base_dir / "subcontractors"
        for index, (sub_preset, sub_scenario) in enumerate(SUBCONTRACTOR_PRESETS):
            company_name = SUBCONTRACTOR_NAMES[sub_preset]
            if unique_subcontractors:
                company_name = (
                    f"{company_name.replace(' LLC', '')} "
                    f"{project.city} Demo LLC"
                )

            sub_result = generate_demo_company_package(
                preset="apex-concrete",
                scenario=sub_scenario,
                output=subs_dir,
                seed=seed + index,
                create_zip=False,
                create_records=False,
                company_name=company_name,
                trade=SUBCONTRACTOR_TRADES[sub_preset],
            )
            subcontractors.append(sub_result)

    manifest = _project_manifest(
        project=project,
        documents=documents,
        subcontractors=subcontractors,
        scenario=scenario,
    )
    manifest_path = base_dir / "project_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    records = None
    if create_records:
        records = create_project_records(
            project=project,
            documents=documents,
            subcontractors=subcontractors,
        )

    return {
        "project": project,
        "output_dir": base_dir,
        "manifest_path": manifest_path,
        "documents": documents,
        "subcontractors": subcontractors,
        "records": records,
    }


def generate_demo_environment(
    *,
    preset="full-demo",
    seed=123,
    scenario="mixed",
    output="instance/demo_environment",
    create_records=False,
):
    if preset != "full-demo":
        raise ValueError("Unsupported demo environment preset.")

    base_dir = Path(output) / "full_demo"
    base_dir.mkdir(parents=True, exist_ok=True)
    project_results = []

    for project in list_demo_projects(seed):
        project_results.append(
            generate_demo_project_package(
                preset=project.key,
                scenario=scenario,
                output=base_dir / "projects",
                seed=seed,
                populate=True,
                create_records=create_records,
                unique_subcontractors=True,
            )
        )

    stats = {"READY": 0, "PENDING": 0, "BLOCKED": 0}
    for project_result in project_results:
        for sub_result in project_result["subcontractors"]:
            stats[sub_result["scenario"].expected_status] += 1

    manifest = {
        "preset": preset,
        "scenario": scenario,
        "statistics": stats,
        "projects": [
            _project_manifest(
                project=result["project"],
                documents=result["documents"],
                subcontractors=result["subcontractors"],
                scenario=scenario,
            )
            for result in project_results
        ],
    }
    manifest_path = base_dir / "environment_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return {
        "output_dir": base_dir,
        "manifest_path": manifest_path,
        "projects": project_results,
        "statistics": stats,
    }


def create_project_records(*, project, documents, subcontractors):
    user = User.query.order_by(User.id.asc()).first()
    if not user:
        raise ValueError("No local user exists for demo record creation.")

    organization = _organization_for_user(user)
    if not organization:
        raise ValueError("Selected user does not belong to an organization.")

    project_record = (
        Project.query
        .filter_by(organization_id=organization.id, name=project.name)
        .first()
    )
    if not project_record:
        project_record = Project(
            name=project.name,
            contract_value=project.contract_value,
            required_coverage=project.required_insurance_limit,
            start_date=project.start_date,
            end_date=project.substantial_completion,
            user_id=user.id,
            organization_id=organization.id,
        )
        db.session.add(project_record)
        db.session.flush()

    for result in documents:
        _create_document_once(
            result=result,
            user_id=user.id,
            project_id=project_record.id,
        )

    for sub_result in subcontractors:
        company = sub_result["company"]
        scenario = sub_result["scenario"]
        subcontractor = (
            Subcontractor.query
            .filter_by(
                organization_id=organization.id,
                name=company.legal_name,
            )
            .first()
        )
        if not subcontractor:
            subcontractor = Subcontractor(
                name=company.legal_name,
                email=company.email,
                phone=company.phone,
                role=company.trade,
                coi_expiration=scenario.policy.expiration_date,
                timezone="America/Chicago",
                user_id=user.id,
                organization_id=organization.id,
            )
            db.session.add(subcontractor)
            db.session.flush()

        link = (
            ProjectSubcontractor.query
            .filter_by(
                project_id=project_record.id,
                subcontractor_id=subcontractor.id,
            )
            .first()
        )
        if not link:
            db.session.add(
                ProjectSubcontractor(
                    project_id=project_record.id,
                    subcontractor_id=subcontractor.id,
                    coverage_limit=scenario.policy.general_liability_each_occurrence,
                )
            )

        for result in sub_result["documents"]:
            _create_document_once(
                result=result,
                user_id=user.id,
                sub_id=subcontractor.id,
            )

    db.session.commit()

    return {
        "project_id": project_record.id,
        "subcontractor_count": len(subcontractors),
        "project_document_count": len(documents),
    }


def register_demo_project_cli(app):
    @app.cli.group("demo-project")
    def demo_project_group():
        """Generate internal BuildSure demo projects."""

    @demo_project_group.command("generate")
    @click.option("--preset", default="summit-distribution-center", show_default=True)
    @click.option("--scenario", default="ready", show_default=True)
    @click.option("--output", default="instance/demo_projects", show_default=True)
    @click.option("--seed", default=123, type=int, show_default=True)
    @click.option("--populate", is_flag=True)
    @click.option("--create-records", is_flag=True)
    def generate_project_command(preset, scenario, output, seed, populate, create_records):
        result = generate_demo_project_package(
            preset=preset,
            scenario=scenario,
            output=output,
            seed=seed,
            populate=populate,
            create_records=create_records,
        )
        click.echo(f"Generated demo project: {result['project'].name}")
        click.echo(f"Output: {result['output_dir']}")
        click.echo(f"Project documents: {len(result['documents'])}")
        click.echo(f"Subcontractors: {len(result['subcontractors'])}")

    @app.cli.group("demo-environment")
    def demo_environment_group():
        """Generate an internal BuildSure full demo environment."""

    @demo_environment_group.command("generate")
    @click.option("--preset", default="full-demo", show_default=True)
    @click.option("--seed", default=123, type=int, show_default=True)
    @click.option("--scenario", default="mixed", show_default=True)
    @click.option("--output", default="instance/demo_environment", show_default=True)
    @click.option("--create-records", is_flag=True)
    def generate_environment_command(preset, seed, scenario, output, create_records):
        result = generate_demo_environment(
            preset=preset,
            seed=seed,
            scenario=scenario,
            output=output,
            create_records=create_records,
        )
        click.echo("Generated demo environment")
        click.echo(f"Output: {result['output_dir']}")
        click.echo(f"Projects: {len(result['projects'])}")
        click.echo(f"Statistics: {result['statistics']}")


def _find_project(preset, seed):
    for project in list_demo_projects(seed):
        if project.key == preset:
            return project

    raise ValueError("Unsupported demo project preset.")


def _project_manifest(*, project, documents, subcontractors, scenario):
    return {
        "project": project_to_json(project),
        "scenario": scenario,
        "documents": [
            {
                "filename": result.filename,
                "document_type": result.document_type,
                "sha256": result.sha256,
                "page_count": result.page_count,
                "expected_extracted_fields": result.expected_extracted_fields,
            }
            for result in documents
        ],
        "subcontractors": [
            {
                "name": result["company"].legal_name,
                "scenario": result["scenario"].key,
                "status": result["scenario"].expected_status,
                "documents": [
                    {
                        "filename": document.filename,
                        "document_type": document.document_type,
                        "sha256": document.sha256,
                        "page_count": document.page_count,
                    }
                    for document in result["documents"]
                ],
            }
            for result in subcontractors
        ],
    }


def _create_document_once(*, result, user_id, project_id=None, sub_id=None):
    existing = Document.query.filter_by(
        project_id=project_id,
        sub_id=sub_id,
        original_name=result.filename,
    ).first()
    if existing:
        return existing

    with open(result.path, "rb") as file:
        storage = FileStorage(
            stream=file,
            filename=result.filename,
            content_type="application/pdf",
        )
        storage_key = save_document_file(
            storage,
            result.filename,
            project_id=project_id,
            sub_id=sub_id,
        )

    document = Document(
        filename=storage_key,
        original_name=result.filename,
        document_type=result.document_type,
        project_id=project_id,
        sub_id=sub_id,
        uploaded_by=user_id,
        ai_status="not_analyzed",
    )
    db.session.add(document)
    return document


def _organization_for_user(user):
    membership = sorted(
        user.organization_memberships,
        key=lambda item: item.id,
    )[0] if user.organization_memberships else None

    return membership.organization if membership else None
