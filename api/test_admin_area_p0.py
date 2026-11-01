"""Admin area P0: billing writes, role permissions, system roles and global invitations.

Denied cases use real roles (a company owner who is not staff, a plain user), never is_staff.
"""
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from api.enterprise_models import Company, CompanyInvitation, CompanyMembership
from api.models import Permission, Plan, PlanLimit, Resource, Role, Subscription, User


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


class RolePermissionsTests(AdminAreaBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        res = [Resource.objects.create(key=f"p0.res{i}", name=f"Res {i}") for i in range(15)]
        cls.perms = [Permission.objects.create(resource=r, action=a, code=f"{r.key}.{a}") for r in res for a in ("view", "manage")]
        cls.custom = Role.objects.create(name="p0.custom", description="custom")
        cls.custom.permissions.set(cls.perms[:3])
        cls.system = Role.objects.update_or_create(name="client", defaults={"is_system": True})[0]

    def test_all_permissions_unpaginated_and_grouped(self):
        r = self.as_(self.staff).get("/api/permissions/all/")
        self.assertEqual(r.status_code, 200)
        ids = {p["id"] for g in r.data for p in g["permissions"]}
        self.assertEqual(len(ids), Permission.objects.count())  # more than one page (30 > 10)
        group = next(g for g in r.data if g["resource_key"] == "p0.res0")
        self.assertEqual(group["resource_name"], "Res 0")
        self.assertEqual({p["action"] for p in group["permissions"]}, {"view", "manage"})

    def test_role_detail_keeps_old_fields_and_adds_new_ones(self):
        r = self.as_(self.staff).get(f"/api/roles/{self.custom.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue({"id", "name", "description", "permissions", "permission_ids", "is_system"} <= set(r.data))
        self.assertEqual(sorted(r.data["permissions"]), sorted(p.id for p in self.perms[:3]))
        self.assertEqual(r.data["permission_ids"], sorted(p.id for p in self.perms[:3]))
        self.assertIs(r.data["is_system"], False)

    def test_put_permissions_replaces_the_whole_set(self):
        new_ids = [p.id for p in self.perms[10:25]]
        r = self.as_(self.staff).put(f"/api/roles/{self.custom.id}/permissions/", {"permission_ids": new_ids}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(self.custom.permissions.values_list("id", flat=True)), sorted(new_ids))

    def test_put_permissions_rejects_unknown_ids_without_changes(self):
        before = sorted(self.custom.permissions.values_list("id", flat=True))
        r = self.as_(self.staff).put(f"/api/roles/{self.custom.id}/permissions/", {"permission_ids": [self.perms[0].id, 99999999]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("permission_ids", r.data)
        self.assertEqual(sorted(self.custom.permissions.values_list("id", flat=True)), before)

    def test_system_role_cannot_be_deleted_or_renamed_but_description_changes(self):
        c = self.as_(self.staff)
        r = c.delete(f"/api/roles/{self.system.id}/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data["detail"], "System roles cannot be deleted.")
        self.assertEqual(c.patch(f"/api/roles/{self.system.id}/", {"name": "renamed"}, format="json").status_code, 400)
        self.assertEqual(c.patch(f"/api/roles/{self.system.id}/", {"description": "new text"}, format="json").status_code, 200)
        self.assertEqual(c.patch(f"/api/roles/{self.system.id}/", {"is_system": False}, format="json").status_code, 200)
        self.system.refresh_from_db()
        self.assertEqual((self.system.name, self.system.is_system), ("client", True))

    def test_custom_role_can_be_deleted(self):
        self.assertEqual(self.as_(self.staff).delete(f"/api/roles/{self.custom.id}/").status_code, 204)

    def test_rbac_admin_endpoints_denied_to_owner_and_member(self):
        for who in (self.user, self.owner):
            c = self.as_(who)
            self.assertEqual(c.get("/api/permissions/all/").status_code, 403)
            self.assertEqual(c.put(f"/api/roles/{self.custom.id}/permissions/", {"permission_ids": []}, format="json").status_code, 403)
            self.assertEqual(c.delete(f"/api/roles/{self.custom.id}/").status_code, 403)


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


class SelfEscalationTests(APITestCase):
    """Nobody can make themselves staff or admin through sign-up or their own profile."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_role = Role.objects.get_or_create(name="admin")[0]
        cls.user = User.objects.create_user(username="p0_self", email="p0_self@example.com", password="x", first_name="Old")

    def test_register_ignores_privilege_fields(self):
        self.client.raise_request_exception = False  # the verification email may fail in tests; the user is saved first
        self.client.post("/api/auth/register/", {
            "username": "p0_signup", "email": "p0_signup@example.com", "password": "Str0ngPass!x9", "first_name": "Ana",
            "is_staff": True, "roles": [self.admin_role.id],
        }, format="json")
        u = User.objects.get(username="p0_signup")
        self.assertFalse(u.is_staff)
        self.assertNotIn("admin", set(u.roles.values_list("name", flat=True)))
        self.assertEqual(u.first_name, "Ana")

    def test_profile_patch_ignores_privilege_fields_but_updates_profile(self):
        self.client.force_authenticate(self.user)
        r = self.client.patch("/api/auth/me/", {"first_name": "New", "is_staff": True, "is_active": False,
                                                 "roles": [self.admin_role.id]}, format="json")
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "New")
        self.assertFalse(self.user.is_staff)
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.roles.filter(name="admin").exists())

