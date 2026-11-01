"""Admin area P1: readable `*_detail` fields, optional search/filters/ordering, no N+1.

Contract tests: the fields the admin pages already read are still there.
N+1 tests: the query count of a page does not grow with the number of rows.
"""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from api.enterprise_learning_models import LearningPath
from api.enterprise_models import Company, CompanyMembership
from api.models import Battery, Deck, Flashcard, Plan, PlanLimit, Project, Role, Subscription, SupportRequest, User


def rows(resp):
    return resp.data["results"] if isinstance(resp.data, dict) and "results" in resp.data else resp.data


class AdminP1Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(username="p1_staff", email="p1_staff@example.com", password="x", is_staff=True)
        cls.owner = User.objects.create_user(username="p1_owner", email="p1_owner@example.com", password="x")
        cls.company = Company.objects.create(name="P1 Co", slug="p1-co", owner=cls.owner)
        CompanyMembership.objects.create(user=cls.owner, company=cls.company, role="owner", status="active")
        cls.free = Plan.objects.get_or_create(tier="free", defaults={"name": "Free"})[0]

    def setUp(self):
        self.client.force_authenticate(self.staff)

    def make_rows(self, n, start=0):
        for i in range(start, start + n):
            u = User.objects.create_user(username=f"p1_u{i}", email=f"p1_u{i}@example.com", password="x")
            u.roles.add(Role.objects.get_or_create(name="client")[0])
            CompanyMembership.objects.create(user=u, company=self.company, role="employee", status="active")
            Subscription.objects.update_or_create(user=u, defaults={"plan": self.free})
            p = Project.objects.create(title=f"P1 project {i}", owner=u)
            d = Deck.objects.create(title=f"P1 deck {i}", owner=u, project=p)
            Flashcard.objects.create(deck=d, front=f"front {i}", back=f"back {i}")
            Battery.objects.create(name=f"P1 battery {i}", project=p)
            LearningPath.objects.create(company=self.company, name=f"P1 path {i}", created_by=u)
            SupportRequest.objects.create(name=f"Lead {i}", email=f"lead{i}@example.com", message=f"Help {i}")
        PlanLimit.objects.get_or_create(plan=self.free, key=f"p1_limit_{start}", defaults={"value_type": "int", "int_value": 1})

    def query_count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return len(ctx)

    def assert_no_n_plus_one(self, url):
        self.make_rows(2)
        few = self.query_count(url)
        self.make_rows(4, start=2)
        many = self.query_count(url)
        self.assertEqual(few, many, f"{url}: {few} queries with fewer rows, {many} with more")


class DetailFieldsTests(AdminP1Base):
    def test_decks_keep_old_fields_and_add_details(self):
        self.make_rows(1)
        row = next(r for r in rows(self.client.get("/api/admin/decks/")) if r["title"] == "P1 deck 0")
        for f in ("id", "ownerId", "title", "visibility", "cardsCount", "project"):
            self.assertIn(f, row)
        self.assertEqual(row["owner_detail"]["username"], "p1_u0")
        self.assertEqual(row["project_detail"]["title"], "P1 project 0")
        self.assertEqual(row["cards_count"], 1)

    def test_flashcards_add_deck_detail(self):
        self.make_rows(1)
        row = next(r for r in rows(self.client.get("/api/admin/flashcards/?search=front 0")))
        self.assertIn("deck", row)
        self.assertEqual(row["deck_detail"]["title"], "P1 deck 0")

    def test_subscriptions_and_limits_add_details(self):
        self.make_rows(1)
        sub = next(r for r in rows(self.client.get("/api/subscriptions/?search=p1_u0")))
        self.assertEqual({"user", "plan", "status"} - set(sub), set())
        self.assertEqual(sub["user_detail"]["email"], "p1_u0@example.com")
        self.assertEqual(sub["plan_detail"]["tier"], "free")
        lim = rows(self.client.get(f"/api/plan-limits/?plan={self.free.id}"))[0]
        self.assertEqual(lim["plan_detail"]["id"], self.free.id)

    def test_projects_batteries_and_learning_paths(self):
        self.make_rows(1)
        proj = rows(self.client.get("/api/admin/projects/?search=P1 project 0"))[0]
        self.assertEqual(proj["owner_detail"]["username"], "p1_u0")
        self.assertIn("documents_count", proj)
        bat = rows(self.client.get("/api/admin/batteries/?search=P1 battery 0"))[0]
        self.assertEqual(bat["project_detail"]["title"], "P1 project 0")
        self.assertIsNone(bat["rule_detail"])
        self.assertEqual(bat["question_count"], 0)
        lp = rows(self.client.get(f"/api/admin/learning-paths/?company={self.company.id}&search=P1 path 0"))[0]
        self.assertEqual(lp["name"], "P1 path 0")
        self.assertEqual(lp["company_detail"], {"id": self.company.id, "name": "P1 Co"})
        self.assertEqual(lp["modules_count"], 0)


class SearchFilterTests(AdminP1Base):
    def test_no_params_means_same_listing(self):
        self.make_rows(2)
        a = [r["id"] for r in rows(self.client.get("/api/admin/decks/"))]
        b = [r["id"] for r in rows(self.client.get("/api/admin/decks/?ordering=not_allowed&project=abc"))]
        self.assertEqual(a, b)  # unknown ordering / invalid filter values are ignored

    def test_users_filters(self):
        self.make_rows(2)
        names = {r["username"] for r in rows(self.client.get(f"/api/users/?company={self.company.id}&company_role=employee&page_size=50"))}
        self.assertEqual(names, {"p1_u0", "p1_u1"})
        staff = {r["username"] for r in rows(self.client.get("/api/users/?is_staff=true&page_size=50"))}
        self.assertIn("p1_staff", staff)
        self.assertNotIn("p1_u0", staff)
        self.assertEqual(rows(self.client.get("/api/users/?ordering=-username&search=p1_u"))[0]["username"], "p1_u1")

    def test_support_search_and_status(self):
        self.make_rows(2)
        r = rows(self.client.get("/api/support-requests/?search=Help 1"))
        self.assertEqual([x["name"] for x in r], ["Lead 1"])
        self.assertEqual(len(rows(self.client.get("/api/support-requests/?status=closed&search=Lead"))), 0)


class NoNPlusOneTests(AdminP1Base):
    def test_decks(self): self.assert_no_n_plus_one("/api/admin/decks/")
    def test_flashcards(self): self.assert_no_n_plus_one("/api/admin/flashcards/")
    def test_projects(self): self.assert_no_n_plus_one("/api/admin/projects/")
    def test_batteries(self): self.assert_no_n_plus_one("/api/admin/batteries/")
    def test_learning_paths(self): self.assert_no_n_plus_one("/api/admin/learning-paths/")
    def test_subscriptions(self): self.assert_no_n_plus_one("/api/subscriptions/")
    def test_users(self): self.assert_no_n_plus_one("/api/users/")
    def test_support(self): self.assert_no_n_plus_one("/api/support-requests/")


class DeniedTests(AdminP1Base):
    def test_new_admin_listings_denied_to_company_owner(self):
        self.client.force_authenticate(self.owner)
        for url in ("/api/admin/batteries/", "/api/admin/learning-paths/", "/api/users/", "/api/support-requests/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)


class CompanyContentTests(AdminP1Base):
    """Company batteries/decks have no Project: company and owner come from their process."""

    def test_battery_and_deck_under_a_process_topic(self):
        from api.enterprise.tests.process_fixtures import make_process_with_generated_topic
        from api.models import BatterySourceTagGroup, DeckSourceTagGroup
        _ks, topic, _doc, _section = make_process_with_generated_topic(self.company, self.owner, title="P1 process")
        battery = Battery.objects.create(name="P1 company battery")
        BatterySourceTagGroup.objects.create(battery=battery, tag_group=topic)
        deck = Deck.objects.create(title="P1 company deck", owner=self.owner)
        DeckSourceTagGroup.objects.create(deck=deck, tag_group=topic)

        bat = rows(self.client.get("/api/admin/batteries/?search=P1 company battery"))[0]
        self.assertIsNone(bat["project_detail"])
        self.assertEqual(bat["company_detail"], {"id": self.company.id, "name": "P1 Co"})
        self.assertEqual(bat["owner_detail"]["username"], "p1_owner")
        dk = rows(self.client.get("/api/admin/decks/?search=P1 company deck"))[0]
        self.assertEqual(dk["company_detail"], {"id": self.company.id, "name": "P1 Co"})

    def test_personal_battery_keeps_project_owner_and_no_company(self):
        self.make_rows(1)
        bat = rows(self.client.get("/api/admin/batteries/?search=P1 battery 0"))[0]
        self.assertEqual(bat["owner_detail"]["username"], "p1_u0")
        self.assertIsNone(bat["company_detail"])
