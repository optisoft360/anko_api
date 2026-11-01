"""Admin area P2: permission search, plan subscriber counts, support inbox, platform stats."""
from django.core.cache import cache
from rest_framework.test import APITestCase

from api.enterprise_models import Company, CompanyMembership
from api.models import Permission, Plan, Resource, Role, Subscription, SupportRequest, User


def rows(resp):
    return resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data


class AdminP2Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(username="p2_staff", email="p2_staff@example.com", password="x", is_staff=True)
        cls.user = User.objects.create_user(username="p2_user", email="p2_user@example.com", password="x")
        cls.owner = User.objects.create_user(username="p2_owner", email="p2_owner@example.com", password="x")
        cls.company = Company.objects.create(name="P2 Co", slug="p2-co", owner=cls.owner)
        CompanyMembership.objects.create(user=cls.owner, company=cls.company, role="owner", status="active")
        cls.plan = Plan.objects.get_or_create(tier="free", defaults={"name": "Free"})[0]
        Subscription.objects.update_or_create(user=cls.user, defaults={"plan": cls.plan})

    def as_(self, who):
        self.client.force_authenticate(who)
        return self.client


class PermissionsListTests(AdminP2Base):
    def test_search_and_action_filter(self):
        res = Resource.objects.create(key="p2.special.route", name="Special")
        Permission.objects.create(resource=res, action="view")
        Permission.objects.create(resource=res, action="manage")
        r = self.as_(self.staff).get("/api/permissions/?search=p2.special&action=manage")
        got = rows(r)
        self.assertEqual([(p["resourceKey"], p["action"]) for p in got], [("p2.special.route", "manage")])


class PlanSubscribersTests(AdminP2Base):
    def test_count_only_for_platform_admins(self):
        admin_row = next(p for p in rows(self.as_(self.staff).get("/api/plans/")) if p["id"] == self.plan.id)
        self.assertEqual(admin_row["subscribers_count"], Subscription.objects.filter(plan=self.plan).count())
        self.client.force_authenticate(None)
        public_row = next(p for p in rows(self.client.get("/api/plans/")) if p["id"] == self.plan.id)
        self.assertIsNone(public_row["subscribers_count"])
        for f in ("id", "tier", "name", "price_cents", "is_active", "limits"):
            self.assertIn(f, public_row)


class SupportInboxTests(AdminP2Base):
    def setUp(self):
        self.req = SupportRequest.objects.create(name="Lead", email="lead@example.com", message="Please call me back soon")

    def test_public_create_unchanged_and_cannot_set_inbox_fields(self):
        self.client.force_authenticate(None)
        r = self.client.post("/api/support-requests/", {"name": "Anon", "email": "anon@example.com",
                                                        "message": "I need help with my account",
                                                        "status": "done", "internal_note": "x", "assigned_to": self.staff.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        obj = SupportRequest.objects.get(email="anon@example.com")
        self.assertEqual((obj.status, obj.internal_note, obj.assigned_to_id), ("new", "", None))

    def test_admin_triage(self):
        c = self.as_(self.staff)
        r = c.patch(f"/api/support-requests/{self.req.id}/", {"status": "done", "assigned_to": self.staff.id, "internal_note": "Called"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["assigned_to_detail"]["username"], "p2_staff")
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.resolved_at)
        self.assertEqual(self.req.internal_note, "Called")
        c.patch(f"/api/support-requests/{self.req.id}/", {"status": "in_progress"}, format="json")
        self.req.refresh_from_db()
        self.assertIsNone(self.req.resolved_at)
        # message is read-only for admins too
        c.patch(f"/api/support-requests/{self.req.id}/", {"message": "changed text here"}, format="json")
        self.req.refresh_from_db()
        self.assertEqual(self.req.message, "Please call me back soon")

    def test_only_platform_admins_can_be_assigned(self):
        r = self.as_(self.staff).patch(f"/api/support-requests/{self.req.id}/", {"assigned_to": self.user.id}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("assigned_to", r.data)

    def test_owner_cannot_triage(self):
        r = self.as_(self.owner).patch(f"/api/support-requests/{self.req.id}/", {"status": "done"}, format="json")
        self.assertEqual(r.status_code, 403)


class PlatformStatsTests(AdminP2Base):
    def setUp(self):
        cache.delete("admin_platform_stats")

    def test_staff_gets_counters(self):
        r = self.as_(self.staff).get("/api/admin/platform/stats/")
        self.assertEqual(r.status_code, 200)
        for k in ("companies", "users", "subscriptions", "content", "invitations", "support", "generation_jobs"):
            self.assertIn(k, r.data)
        self.assertEqual(r.data["companies"]["total"], Company.objects.count())
        self.assertGreaterEqual(r.data["users"]["staff"], 1)

    def test_owner_and_member_denied(self):
        for who in (self.owner, self.user):
            self.assertEqual(self.as_(who).get("/api/admin/platform/stats/").status_code, 403)


class AdminLearningPathDetailTests(AdminP2Base):
    def setUp(self):
        from api.enterprise_learning_models import LearningPath
        self.lp = LearningPath.objects.create(company=self.company, name="Onboarding", created_by=self.owner)

    def test_staff_edits_only_allowed_fields_and_deletes(self):
        c = self.as_(self.staff)
        r = c.patch(f"/api/admin/learning-paths/{self.lp.id}/", {"name": "Onboarding v2", "status": "published", "company": 999}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.lp.refresh_from_db()
        self.assertEqual((self.lp.name, self.lp.status, self.lp.company_id), ("Onboarding v2", "published", self.company.id))
        self.assertEqual(r.data["company_detail"]["name"], "P2 Co")
        self.assertEqual(c.delete(f"/api/admin/learning-paths/{self.lp.id}/").status_code, 204)

    def test_owner_denied(self):
        self.assertEqual(self.as_(self.owner).delete(f"/api/admin/learning-paths/{self.lp.id}/").status_code, 403)
