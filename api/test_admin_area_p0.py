"""Admin area P0: billing writes and global invitations.

Denied cases use real roles (a company owner who is not staff, a plain user), never is_staff.
"""
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from api.enterprise_models import Company, CompanyInvitation, CompanyMembership
from api.models import Plan, PlanLimit, Role, Subscription, User


class AdminAreaBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(username="p0_staff", email="p0_staff@example.com", password="x", is_staff=True)
        cls.role_admin = User.objects.create_user(username="p0_roleadmin", email="p0_roleadmin@example.com", password="x")
        cls.role_admin.roles.add(Role.objects.get_or_create(name="admin")[0])
        cls.user = User.objects.create_user(username="p0_user", email="p0_user@example.com", password="x")
        cls.owner = User.objects.create_user(username="p0_owner", email="p0_owner@example.com", password="x")
        cls.company = Company.objects.create(name="P0 Co", slug="p0-co", owner=cls.owner)
        CompanyMembership.objects.create(user=cls.owner, company=cls.company, role="owner", status="active")

        cls.free = Plan.objects.get_or_create(tier="free", defaults={"name": "Free"})[0]
        cls.premium = Plan.objects.get_or_create(tier="premium", defaults={"name": "Premium", "price_cents": 999})[0]
        cls.limit = PlanLimit.objects.get_or_create(plan=cls.free, key="p0_limit", defaults={"value_type": "int", "int_value": 5})[0]
        cls.sub = Subscription.objects.update_or_create(user=cls.user, defaults={"plan": cls.free, "status": "active"})[0]

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client


class BillingWritesTests(AdminAreaBase):
    def test_anonymous_cannot_change_a_plan_but_can_list_plans(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.patch(f"/api/plans/{self.free.id}/", {"price_cents": 1}, format="json").status_code, (401, 403))
        self.assertEqual(self.client.get("/api/plans/").status_code, 200)
        self.free.refresh_from_db()
        self.assertNotEqual(self.free.price_cents, 1)

    def test_member_and_company_owner_cannot_write_plans_or_limits(self):
        for who in (self.user, self.owner):
            c = self.as_(who)
            self.assertEqual(c.patch(f"/api/plans/{self.free.id}/", {"price_cents": 1}, format="json").status_code, 403)
            self.assertEqual(c.delete(f"/api/plans/{self.free.id}/").status_code, 403)
            self.assertEqual(c.patch(f"/api/plan-limits/{self.limit.id}/", {"int_value": 999}, format="json").status_code, 403)
            self.assertEqual(c.get("/api/plan-limits/").status_code, 200)  # reads unchanged
        self.limit.refresh_from_db()
        self.assertEqual(self.limit.int_value, 5)

    def test_member_cannot_upgrade_own_subscription(self):
        c = self.as_(self.user)
        self.assertEqual(c.patch(f"/api/subscriptions/{self.sub.id}/", {"plan": self.premium.id}, format="json").status_code, 403)
        self.assertEqual(c.post("/api/subscriptions/set-plan/", {"tier": "premium"}, format="json").status_code, 403)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan_id, self.free.id)

    def test_member_still_reads_own_subscription(self):
        c = self.as_(self.user)
        self.assertEqual(c.get("/api/subscriptions/me/").status_code, 200)
        r = c.get("/api/subscriptions/")
        self.assertEqual(r.status_code, 200)
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual({row["user"] for row in rows}, {self.user.id})

    def test_platform_admins_can_write_billing(self):
        for who in (self.staff, self.role_admin):
            c = self.as_(who)
            self.assertEqual(c.patch(f"/api/plans/{self.premium.id}/", {"price_cents": 1299}, format="json").status_code, 200)
            self.assertEqual(c.patch(f"/api/plan-limits/{self.limit.id}/", {"int_value": 7}, format="json").status_code, 200)
            self.assertEqual(c.patch(f"/api/subscriptions/{self.sub.id}/", {"status": "active"}, format="json").status_code, 200)

    def test_role_admin_lists_every_subscription(self):
        r = self.as_(self.role_admin).get("/api/subscriptions/")
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertIn(self.sub.id, {row["id"] for row in rows})


class GlobalInvitationsTests(AdminAreaBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.inv = CompanyInvitation.objects.create(
            company=cls.company, email="new@example.com", role="employee", token=uuid.uuid4(),
            expires_at=timezone.now() + timedelta(days=7), invited_by=cls.owner,
        )

    def test_staff_and_role_admin_list_with_details(self):
        for who in (self.staff, self.role_admin):
            r = self.as_(who).get("/api/enterprise/invitations/global/")
            self.assertEqual(r.status_code, 200)
            row = next(x for x in r.data if x["id"] == self.inv.id)
            # old contract still there
            for f in ("company", "company_name", "email", "role", "status", "invited_by", "invited_by_email", "expires_at", "created_at"):
                self.assertIn(f, row)
            self.assertEqual(row["company_detail"], {"id": self.company.id, "name": "P0 Co"})
            self.assertEqual(row["invited_by_detail"], {"id": self.owner.id, "username": "p0_owner", "email": "p0_owner@example.com"})

    def test_owner_and_member_denied(self):
        for who in (self.owner, self.user):
            self.assertEqual(self.as_(who).get("/api/enterprise/invitations/global/").status_code, 403)
