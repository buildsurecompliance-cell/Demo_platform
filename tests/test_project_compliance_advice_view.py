import os
import unittest

from types import SimpleNamespace
from unittest.mock import patch


os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import create_app
from app.extensions import db
from app.models import Project, ProjectSubcontractor, Subcontractor, User
from app.routes import projects as project_routes


class ProjectComplianceAdviceViewTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app()
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
            db.session.commit()
            self.user_id = self.user.id
            self.other_user_id = self.other_user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def make_project_with_subs(self, statuses):
        with self.app.app_context():
            project = Project(
                name="Test Project",
                user_id=self.user_id,
            )
            db.session.add(project)
            db.session.flush()

            for index, status in enumerate(statuses, start=1):
                sub = Subcontractor(
                    name=f"Sub {index}",
                    user_id=self.user_id,
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
        self.assertIn("Ready for mobilization.", body)
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
            },
        )
        self.assertTrue(row["advice_available"])
        self.assertEqual(row["advice"].status, "READY")

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
        self.assertIn(
            row["advice"].status,
            {
                "READY",
                "PENDING",
                "BLOCKED",
            },
        )

    def test_ready_renders_summary_without_empty_actions(self):
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
        self.assertIn("Ready for mobilization.", body)
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
        self.assertIn("MEDIUM", body)

    def test_blocked_renders_summary_and_priority_actions(self):
        project_id = self.make_project_with_subs(["BLOCKED"])
        self.login(self.user_id)

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
        self.assertIn("Review insurance coverage.", body)
        self.assertIn("HIGH", body)

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
        self.assertIn("Ready for mobilization.", body)

    def test_user_cannot_access_project_from_other_user(self):
        with self.app.app_context():
            project = Project(
                name="Other Project",
                user_id=self.other_user_id,
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


if __name__ == "__main__":
    unittest.main()
