import json
import zipfile

from pathlib import Path

import click

from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Document, Project, ProjectSubcontractor, Subcontractor, User
from app.services.demo_company_generator.company_factory import (
    build_apex_concrete_company,
)
from app.services.demo_company_generator.document_builders import DOCUMENT_BUILDERS
from app.services.demo_company_generator.models import dataclass_to_json, utc_now
from app.services.demo_company_generator.scenario_factory import build_scenario
from app.services.documents.storage import save_document_file


PRESETS = {
    "apex-concrete": build_apex_concrete_company,
}


def generate_demo_company_package(
    *,
    preset="apex-concrete",
    scenario="ready",
    output="instance/demo_companies",
    seed=123,
    create_zip=False,
    create_records=False,
    company_name=None,
    trade=None,
):
    if preset not in PRESETS:
        raise ValueError("Unsupported demo company preset.")

    company = PRESETS[preset](seed=seed)

    if company_name:
        object.__setattr__(company, "legal_name", company_name)
        object.__setattr__(company, "dba_name", company_name.replace(" LLC", ""))
        object.__setattr__(
            company,
            "email",
            f"compliance@{_slug(company_name).replace('_llc', '')}-demo.com",
        )

    if trade:
        object.__setattr__(company, "trade", trade)

    demo_scenario = build_scenario(
        company,
        scenario,
    )
    base_dir = Path(output) / _slug(company.legal_name)
    base_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for builder in DOCUMENT_BUILDERS:
        result = builder(
            company,
            demo_scenario,
            base_dir,
        )
        results.append(result)

    company_json = base_dir / "company.json"
    company_payload = dataclass_to_json(company)
    company_payload.update(
        {
            "city": company.city,
            "state": company.state,
            "zip_code": company.zip_code,
        }
    )
    company_json.write_text(
        json.dumps(
            {
                "company": company_payload,
                "scenario": dataclass_to_json(demo_scenario),
                "fictitious": True,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    manifest = _manifest(
        company=company,
        scenario=demo_scenario,
        results=results,
    )
    manifest_path = base_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    zip_path = None
    if create_zip:
        zip_path = base_dir / "Apex_Concrete_LLC_Demo_Package.zip"
        _write_zip(
            zip_path,
            base_dir,
            [
                company_json,
                manifest_path,
                *[result.path for result in results],
            ],
        )

    created_records = None
    if create_records:
        created_records = create_demo_records(
            company=company,
            scenario=demo_scenario,
            documents=results,
        )

    return {
        "company": company,
        "scenario": demo_scenario,
        "output_dir": base_dir,
        "company_json": company_json,
        "manifest_path": manifest_path,
        "documents": results,
        "zip_path": zip_path,
        "records": created_records,
    }


def create_demo_records(*, company, scenario, documents):
    user = User.query.order_by(User.id.asc()).first()
    if not user:
        raise ValueError("No local user exists for demo record creation.")

    organization = _organization_for_user(user)
    if not organization:
        raise ValueError("Selected user does not belong to an organization.")

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
            timezone="America/Chicago",
            coi_expiration=scenario.policy.expiration_date,
            user_id=user.id,
            organization_id=organization.id,
        )
        db.session.add(subcontractor)
        db.session.flush()

    project = (
        Project.query
        .filter_by(
            organization_id=organization.id,
            name="Summit Distribution Center",
        )
        .first()
    )

    if project:
        existing_link = (
            ProjectSubcontractor.query
            .filter_by(
                project_id=project.id,
                subcontractor_id=subcontractor.id,
            )
            .first()
        )
        if not existing_link:
            db.session.add(
                ProjectSubcontractor(
                    project_id=project.id,
                    subcontractor_id=subcontractor.id,
                    coverage_limit=scenario.policy.general_liability_each_occurrence,
                )
            )

    stored_documents = []

    for result in documents:
        existing_doc = (
            Document.query
            .filter_by(
                sub_id=subcontractor.id,
                original_name=result.filename,
            )
            .first()
        )
        if existing_doc:
            stored_documents.append(existing_doc)
            continue

        with open(result.path, "rb") as file:
            storage = FileStorage(
                stream=file,
                filename=result.filename,
                content_type="application/pdf",
            )
            storage_key = save_document_file(
                storage,
                result.filename,
                sub_id=subcontractor.id,
            )

        document = Document(
            filename=storage_key,
            original_name=result.filename,
            document_type=result.document_type,
            sub_id=subcontractor.id,
            uploaded_by=user.id,
            ai_status="not_analyzed",
        )
        db.session.add(document)
        stored_documents.append(document)

    db.session.commit()

    return {
        "user_id": user.id,
        "organization_id": organization.id,
        "subcontractor_id": subcontractor.id,
        "project_id": project.id if project else None,
        "document_count": len(stored_documents),
    }


def register_demo_company_cli(app):
    @app.cli.group("demo-company")
    def demo_company_group():
        """Generate internal BuildSure demo company documents."""

    @demo_company_group.command("generate")
    @click.option("--preset", default="apex-concrete", show_default=True)
    @click.option(
        "--scenario",
        default="ready",
        type=click.Choice(["ready", "pending", "blocked"]),
        show_default=True,
    )
    @click.option("--output", default="instance/demo_companies", show_default=True)
    @click.option("--seed", default=123, type=int, show_default=True)
    @click.option("--zip", "create_zip", is_flag=True)
    @click.option("--create-records", is_flag=True)
    def generate_command(
        preset,
        scenario,
        output,
        seed,
        create_zip,
        create_records,
    ):
        result = generate_demo_company_package(
            preset=preset,
            scenario=scenario,
            output=output,
            seed=seed,
            create_zip=create_zip,
            create_records=create_records,
        )
        click.echo(f"Generated demo company: {result['company'].legal_name}")
        click.echo(f"Output: {result['output_dir']}")
        click.echo(f"Documents: {len(result['documents'])}")
        if result["zip_path"]:
            click.echo(f"ZIP: {result['zip_path']}")
        if result["records"]:
            click.echo(f"Records: {result['records']}")


def _organization_for_user(user):
    membership = sorted(
        user.organization_memberships,
        key=lambda item: item.id,
    )[0] if user.organization_memberships else None

    return membership.organization if membership else None


def _manifest(*, company, scenario, results):
    return {
        "company": company.legal_name,
        "preset": "apex-concrete",
        "scenario": scenario.key,
        "expected_status": scenario.expected_status,
        "generated_at": utc_now().isoformat(),
        "documents": [
            {
                "filename": result.filename,
                "document_type": result.document_type,
                "generated_at": utc_now().isoformat(),
                "sha256": result.sha256,
                "page_count": result.page_count,
                "scenario": scenario.key,
                "expected_extracted_fields": result.expected_extracted_fields,
            }
            for result in results
        ],
    }


def _write_zip(zip_path, base_dir, paths):
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in paths:
            archive.write(
                path,
                arcname=Path(path).relative_to(base_dir),
            )


def _slug(value):
    return (
        value
        .strip()
        .lower()
        .replace("&", "and")
        .replace(" ", "_")
    )
