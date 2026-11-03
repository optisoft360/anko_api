"""seed_business_plans: creates the two company plans, is idempotent, leaves Free/Premium/Ultra alone."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from api.management.commands.seed_business_plans import BUSINESS_PLANS
from api.models import Plan, PlanLimit
from api.services.plan_guard import PlanGuard

LIMIT_COUNT = sum(len(spec["limits"]) for spec in BUSINESS_PLANS)


def snapshot_personal_plans():
    plans = list(Plan.objects.filter(tier__in=["free", "premium", "ultra"]).order_by("tier").values())
    limits = list(
        PlanLimit.objects.filter(plan__tier__in=["free", "premium", "ultra"]).order_by("plan__tier", "key").values()
    )
    return plans, limits


class SeedBusinessPlansTests(TestCase):
    def setUp(self):
        call_command("seed_plans")

    def seed(self, *args):
        out = StringIO()
        call_command("seed_business_plans", *args, stdout=out)
        return out.getvalue()

    def test_first_run_creates_two_plans_and_their_limits(self):
        self.seed()
        pilot = Plan.objects.get(tier="business_pilot")
        business = Plan.objects.get(tier="business")
        self.assertEqual((pilot.price_cents, pilot.max_documents, pilot.max_batteries), (9900, 10, 3))
        self.assertEqual((business.price_cents, business.max_documents), (14900, None))
        self.assertEqual(PlanLimit.objects.filter(plan__in=[pilot, business]).count(), LIMIT_COUNT)
        self.assertEqual(PlanGuard.limit_int(pilot, "employees_max"), 5)
        self.assertEqual(PlanGuard.limit_int(business, "employees_max"), 50)
        self.assertTrue(PlanGuard.limit_bool(pilot, "can_invite"))
        # Registered tier: profile limits are no longer Free's.
        self.assertEqual(PlanGuard.public_tier(pilot), "business_pilot")
        self.assertEqual(PlanGuard._profile_limit_int(pilot, "documents_per_month"), 500)

    def test_second_run_creates_nothing(self):
        self.seed()
        before = (Plan.objects.count(), PlanLimit.objects.count())
        out = self.seed()
        self.assertEqual((Plan.objects.count(), PlanLimit.objects.count()), before)
        self.assertIn(f"created=0, updated=0, unchanged={len(BUSINESS_PLANS) + LIMIT_COUNT}", out)

    def test_personal_plans_and_limits_unchanged(self):
        before = snapshot_personal_plans()
        self.seed()
        self.seed()
        self.assertEqual(snapshot_personal_plans(), before)

    def test_dry_run_writes_nothing(self):
        before = (Plan.objects.count(), PlanLimit.objects.count())
        out = self.seed("--dry-run")
        self.assertEqual((Plan.objects.count(), PlanLimit.objects.count()), before)
        self.assertFalse(Plan.objects.filter(tier__in=["business_pilot", "business"]).exists())
        self.assertIn("DRY RUN", out)
