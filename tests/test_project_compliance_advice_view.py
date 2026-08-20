import os
import unittest

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    DOCUMENT_REQUEST_COMPLETED,
    DOCUMENT_REQUEST_PENDING,
    Document,
    DocumentRequest,
    Project,
    ProjectSubcontractor,
    Subcontractor,
    User,
)
from app.routes import projects as project_routes
from app.services.document_requests import hash_document_request_token
from app.services.organizations import create_default_organization_for_user


class ProjectComplianceAdviceViewTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()

            self.user = User(
                email="owner@example.com",
                paid=True,
            )
            self.user.set_password("password123")

            self.other_user = User(
                email="other@example.com",
                paid=True,
            )
            self.other_user.set_password("password123")

            db.session.add_all(
                [
                    self.user,
                    self.other_user,
                ]
            )
            db.session.flush()
            self.organization = create_default_organization_for_user(self.user)
            self.other_organization = create_default_organization_for_user(
                self.other_user
            )
            db.session.commit()
            self.user_id = self.user.id
            self.other_user_id = self.other_user.id
            self.organization_id = self.organization.id
            self.other_organization_id = self.other_organization.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            if user_id == self.user_id:
                session["active_organization_id"] = self.organization_id
            elif user_id == self.other_user_id:
                session["active_organization_id"] = self.other_organization_id

    def make_project_with_subs(self, statuses):
        with self.app.app_context():
            project = Project(
                name="Test Project",
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
            db.session.add(project)
            db.session.flush()

            for index, status in enumerate(statuses, start=1):
                sub = Subcontractor(
                    name=f"Sub {index}",
                    user_id=self.user_id,
                    organization_id=self.organization_id,
                    role="Trade",
                )
                db.session.add(sub)
                db.session.flush()
                db.session.add(
                    ProjectSubcontractor(
                        project_id=project.id,
                        subcontractor_id=sub.id,
                    )
                )

            db.session.commit()
            return project.id

    def make_advice(self, status, summary, actions=()):
        return SimpleNamespace(
            status=status,
            summary=summary,
            actions=tuple(
                SimpleNamespace(**action)
                for action in actions
            ),
        )

    def test_route_prepares_advice_for_each_project_subcontractor(self):
        project_id = self.make_project_with_subs(
            [
                "READY",
                "PENDING",
            ]
        )
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            side_effect=[
                self.make_advice("READY", "Ready for mobilization."),
                self.make_advice(
                    "PENDING",
                    "Compliance review is pending.",
                ),
            ],
        ) as advice_mock:
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(advice_mock.call_count, 2)
        self.assertIn("READY", body)
        self.assertIn("Compliance review is pending.", body)

    def test_view_model_has_predictable_contract(self):
        project_id = self.make_project_with_subs(["READY"])

        with self.app.app_context():
            link = ProjectSubcontractor.query.filter_by(
                project_id=project_id,
            ).first()

            with patch(
                "app.routes.projects.generate_compliance_advice",
                return_value=self.make_advice(
                    "READY",
                    "Ready for mobilization.",
                ),
            ):
                row = project_routes._project_subcontractor_view_model(link)

        self.assertEqual(
            set(row.keys()),
            {
                "project_subcontractor",
                "advice",
                "advice_available",
                "status",
                "current_coverage",
                "required_coverage",
                "coverage_gap",
                "coi_expiration",
                "primary_reason",
                "recommended_action",
                "action_priority",
                "document_request",
                "document_request_status",
                "document_request_cta",
                "show_operational_action",
                "show_more_actions",
            },
        )
        self.assertTrue(row["advice_available"])
        self.assertEqual(row["advice"].status, "READY")
        self.assertEqual(row["status"], "READY")

    def test_view_model_fallback_has_predictable_contract(self):
        project_id = self.make_project_with_subs(["READY"])

        with self.app.app_context():
            link = ProjectSubcontractor.query.filter_by(
                project_id=project_id,
            ).first()

            with patch(
                "app.routes.projects.generate_compliance_advice",
                side_effect=RuntimeError("boom"),
            ):
                row = project_routes._project_subcontractor_view_model(link)

        self.assertFalse(row["advice_available"])
        self.assertEqual(row["advice"].summary, "Compliance advice unavailable.")
        self.assertEqual(row["advice"].actions, ())
        self.assertIsNone(row["recommended_action"])
        self.assertIn(
            row["advice"].status,
            {
                "READY",
                "PENDING",
                "BLOCKED",
            },
        )

    def test_ready_renders_simple_status_without_empty_actions(self):
        project_id = self.make_project_with_subs(["READY"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "READY",
                "Ready for mobilization.",
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("READY", body)
        self.assertNotIn("Ready for mobilization.", body)
        self.assertNotIn("Recommended Action", body)
        self.assertNotIn("<ul class=\"mb-0\">", body)

    def test_pending_renders_summary_and_actions(self):
        project_id = self.make_project_with_subs(["PENDING"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "PENDING",
                "Compliance review is pending.",
                actions=[
                    {
                        "title": "Wait until document analysis completes.",
                        "description": "Review again after processing.",
                        "priority": "MEDIUM",
                    },
                ],
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("PENDING", body)
        self.assertIn("Compliance review is pending.", body)
        self.assertIn("Wait until document analysis completes.", body)
        self.assertIn("Recommended Action", body)

    def test_blocked_renders_simplified_coverage_action_without_priority(self):
        project_id = self.make_project_with_subs(["BLOCKED"])
        self.login(self.user_id)

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            project.required_coverage = 5_000_000
            link = ProjectSubcontractor.query.filter_by(
                project_id=project_id,
            ).first()
            sub = link.subcontractor
            doc = Document(
                filename="subcontractors/1/coi.pdf",
                original_name="coi.pdf",
                document_type="COI",
                sub_id=sub.id,
                uploaded_by=self.user_id,
                ai_status="analyzed",
                ai_confidence=0.95,
                ai_extracted_data={
                    "expiration_date": "2027-05-01",
                    "coverage_limit": 2_000_000,
                    "general_liability": {
                        "each_occurrence": 2_000_000,
                        "expiration_date": "2027-05-01",
                    },
                    "confidence": 0.95,
                },
                ai_compliance_result={
                    "is_coi": True,
                    "validator": {"valid": True, "errors": []},
                    "confidence": 0.95,
                },
            )
            db.session.add(doc)
            db.session.commit()

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "BLOCKED",
                "Mobilization blocked because insurance coverage is below the project requirement.",
                actions=[
                    {
                        "title": "Review insurance coverage.",
                        "description": "Confirm coverage meets the project requirement.",
                        "priority": "HIGH",
                    },
                ],
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("BLOCKED", body)
        self.assertIn("GL coverage is below requirement.", body)
        self.assertIn(
            "A corrected COI with at least $5M GL coverage is required.",
            body,
        )
        self.assertNotIn("Priority: HIGH", body)
        self.assertNotIn(
            "Confirm coverage meets the project requirement.",
            body,
        )
        self.assertNotIn(
            "Mobilization blocked because insurance coverage is below the project requirement.",
            body,
        )
        self.assertIn("Current GL", body)
        self.assertIn("Required GL", body)
        self.assertIn("Coverage Gap", body)
        self.assertIn("$2M", body)
        self.assertIn("$5M", body)
        self.assertIn("$3M", body)

    def test_project_without_subcontractors_still_renders(self):
        project_id = self.make_project_with_subs([])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
        ) as advice_mock:
            response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "No subcontractors linked.",
            response.get_data(as_text=True),
        )
        advice_mock.assert_not_called()

    def test_compliance_officer_failure_does_not_break_page(self):
        project_id = self.make_project_with_subs(["READY"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Compliance advice unavailable.", body)
        self.assertIn("BLOCKED", body)
        self.assertNotIn("<ul class=\"mb-0\">", body)

    def test_failure_in_one_link_does_not_block_other_links(self):
        project_id = self.make_project_with_subs(
            [
                "BLOCKED",
                "READY",
            ]
        )
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            side_effect=[
                RuntimeError("boom"),
                self.make_advice(
                    "READY",
                    "Ready for mobilization.",
                ),
            ],
        ) as advice_mock:
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(advice_mock.call_count, 2)
        self.assertIn("Compliance advice unavailable.", body)
        self.assertIn("READY", body)

    def test_user_cannot_access_project_from_other_user(self):
        with self.app.app_context():
            project = Project(
                name="Other Project",
                user_id=self.other_user_id,
                organization_id=self.other_organization_id,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login(self.user_id)

        response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 404)

    def test_unauthorized_project_does_not_call_compliance_officer(self):
        with self.app.app_context():
            project = Project(
                name="Other Project",
                user_id=self.other_user_id,
                organization_id=self.other_organization_id,
            )
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        self.login(self.user_id)

        with patch(
            "app.routes.projects.generate_compliance_advice",
        ) as advice_mock:
            response = self.client.get(f"/project/{project_id}")

        self.assertEqual(response.status_code, 404)
        advice_mock.assert_not_called()

    def test_template_does_not_call_business_service_directly(self):
        with open(
            "app/templates/view_project.html",
            encoding="utf-8",
        ) as template:
            content = template.read()

        self.assertNotIn("generate_compliance_advice", content)
        self.assertNotIn("calculate_readiness", content)
        self.assertNotIn("readiness_status", content)

    def test_request_coi_cta_without_previous_request(self):
        project_id = self.make_project_with_subs(["BLOCKED"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "BLOCKED",
                "COI is missing.",
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("Request COI", body)
        self.assertNotIn("Request Corrected COI", body)

    def test_pending_request_shows_sent_and_resend(self):
        project_id = self.make_project_with_subs(["BLOCKED"])

        with self.app.app_context():
            link = ProjectSubcontractor.query.filter_by(
                project_id=project_id,
            ).first()
            request = DocumentRequest(
                organization_id=self.organization_id,
                project_id=project_id,
                subcontractor_id=link.subcontractor_id,
                document_type="COI",
                token_hash=hash_document_request_token("pending-token"),
                status=DOCUMENT_REQUEST_PENDING,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                created_by_user_id=self.user_id,
            )
            request.last_sent_at = datetime(2026, 8, 20, 12, 0, 0)
            db.session.add(request)
            db.session.commit()

        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "BLOCKED",
                "COI is missing.",
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("COI request sent Aug 20", body)
        self.assertIn("Resend", body)
        self.assertNotIn("Request Corrected COI", body)

    def test_completed_blocked_request_shows_corrected_cta(self):
        project_id = self.make_project_with_subs(["BLOCKED"])

        with self.app.app_context():
            project = db.session.get(Project, project_id)
            project.required_coverage = 5_000_000
            link = ProjectSubcontractor.query.filter_by(
                project_id=project_id,
            ).first()
            request = DocumentRequest(
                organization_id=self.organization_id,
                project_id=project_id,
                subcontractor_id=link.subcontractor_id,
                document_type="COI",
                token_hash=hash_document_request_token("completed-token"),
                status=DOCUMENT_REQUEST_COMPLETED,
                expires_at=datetime.now(timezone.utc),
                completed_at=datetime(2026, 8, 20, 12, 0, 0),
                created_by_user_id=self.user_id,
            )
            db.session.add(request)
            db.session.commit()

        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "BLOCKED",
                "COI is still insufficient.",
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("COI received Aug 20", body)
        self.assertIn("Request Corrected COI", body)
        self.assertNotIn(">Request COI</button>", body)

    def test_request_corrected_coi_creates_new_request(self):
        project_id = self.make_project_with_subs(["BLOCKED"])

        with self.app.app_context():
            link = ProjectSubcontractor.query.filter_by(
                project_id=project_id,
            ).first()
            link.subcontractor.email = "sub@example.com"
            old_request = DocumentRequest(
                organization_id=self.organization_id,
                project_id=project_id,
                subcontractor_id=link.subcontractor_id,
                document_type="COI",
                token_hash=hash_document_request_token("completed-token"),
                status=DOCUMENT_REQUEST_COMPLETED,
                expires_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                created_by_user_id=self.user_id,
            )
            db.session.add(old_request)
            db.session.commit()
            old_request_id = old_request.id
            old_token_hash = old_request.token_hash
            subcontractor_id = link.subcontractor_id

        self.login(self.user_id)

        with patch(
            "app.services.document_requests.send_email_reminder",
            return_value=True,
        ):
            response = self.client.post(
                f"/project/{project_id}/subcontractor/{subcontractor_id}/request-coi"
            )

        self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            requests = DocumentRequest.query.order_by(
                DocumentRequest.id.asc()
            ).all()
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0].id, old_request_id)
            self.assertEqual(requests[0].status, DOCUMENT_REQUEST_COMPLETED)
            self.assertEqual(requests[0].token_hash, old_token_hash)
            self.assertEqual(requests[1].status, DOCUMENT_REQUEST_PENDING)
            self.assertNotEqual(requests[1].token_hash, old_token_hash)

    def test_ready_card_does_not_show_correction_cta(self):
        project_id = self.make_project_with_subs(["READY"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "READY",
                "Ready for mobilization.",
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("READY", body)
        self.assertNotIn("Request Corrected COI", body)
        self.assertNotIn("Request COI", body)
        self.assertNotIn("Recommended Action", body)

    def test_summary_and_actions_are_escaped(self):
        project_id = self.make_project_with_subs(["PENDING"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "PENDING",
                "<script>alert('summary')</script>",
                actions=[
                    {
                        "title": "<script>alert('title')</script>",
                        "description": "<script>alert('description')</script>",
                        "priority": "MEDIUM",
                    },
                ],
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertNotIn("<script>alert('summary')</script>", body)
        self.assertNotIn("<script>alert('title')</script>", body)
        self.assertNotIn("<script>alert('description')</script>", body)
        self.assertIn("&lt;script&gt;alert(&#39;summary&#39;)&lt;/script&gt;", body)

    def test_actions_remain_in_officer_order(self):
        project_id = self.make_project_with_subs(["BLOCKED"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "BLOCKED",
                "Mobilization blocked.",
                actions=[
                    {
                        "title": "First action.",
                        "description": "First description.",
                        "priority": "HIGH",
                    },
                    {
                        "title": "Second action.",
                        "description": "Second description.",
                        "priority": "MEDIUM",
                    },
                ],
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertLess(
            body.index("First action."),
            body.index("Second action."),
        )

    def test_actions_are_visual_guidance_not_new_functional_buttons(self):
        project_id = self.make_project_with_subs(["PENDING"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=None,
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "PENDING",
                "Compliance review is pending.",
                actions=[
                    {
                        "title": "Upload a valid Certificate of Insurance.",
                        "description": "Add a current COI.",
                        "priority": "MEDIUM",
                    },
                ],
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("Upload a valid Certificate of Insurance.", body)
        self.assertNotIn("Send Email", body)
        self.assertNotIn("Approve", body)

    def test_project_summary_uses_final_readiness_language(self):
        project_id = self.make_project_with_subs(["BLOCKED"])
        self.login(self.user_id)

        with patch(
            "app.routes.projects.get_project_ai_summary",
            return_value=SimpleNamespace(
                score=100,
                ready=3,
                pending=0,
                blocked=0,
                risk="Low",
                mobilization="Project Ready",
                revenue_at_risk=0,
                critical_issues=[],
            ),
        ), patch(
            "app.routes.projects.generate_compliance_advice",
            return_value=self.make_advice(
                "BLOCKED",
                "Mobilization blocked.",
            ),
        ):
            response = self.client.get(f"/project/{project_id}")

        body = response.get_data(as_text=True)
        self.assertIn("Document Intelligence Summary", body)
        self.assertIn(
            "Document analysis results do not replace final mobilization readiness.",
            body,
        )
        self.assertIn("One or more subcontractors cannot work today.", body)


if __name__ == "__main__":
    unittest.main()
