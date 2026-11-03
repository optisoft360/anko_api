# api/management/commands/seed_business_plans.py
"""
Seed the company plans sold on the landing: Ankard Business Pilot and Ankard Business.

Additive and idempotent: creates or updates only the business_pilot / business plans and their
PlanLimit rows. Free / Premium / Ultra, subscriptions, companies and users are never touched.

    python manage.py seed_business_plans [--dry-run] [--verbose]
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import Plan, PlanLimit

# Same value the app already uses for "unlimited" in PlanLimit (Ultra's flashcards_max_total):
# a null int_value falls back to the defaults in PlanGuard, it does not mean unlimited.
UNLIMITED = 99999

PROTECTED_TIERS = {"free", "premium", "ultra"}

# Ultra's values for the keys inherited from the personal plans.
_INHERITED_LIMITS = {
    "upload_max_mb": ("int", 300),
    "documents_per_month": ("int", 500),
    "pages_per_document_max": ("int", 500),
    "ask_queries_per_month": ("int", 15000),
    "flashcard_jobs_per_month": ("int", 200),
    "flashcards_per_job_max": ("int", 300),
    "history_days": ("int", 365),
    "questions_per_battery_max": ("int", UNLIMITED),
    "explore_topics_limit": ("int", UNLIMITED),
    "can_use_flashcards": ("bool", True),
    "can_collect_batteries": ("bool", True),
    "can_collect_decks": ("bool", True),
}

# Plan fields + limits. Documents and evaluations reuse the existing Plan.max_documents /
# Plan.max_batteries columns (null = unlimited there). Fields Plan has no column for
# (display order, trial, guarantee, scope) are stored as PlanLimit rows.
BUSINESS_PLANS = [
    {
        "tier": "business_pilot",
        "fields": {
            "name": "Ankard Business Pilot",
            "description": "Todo lo necesario para probar Ankard con un equipo pequeño durante 30 días.",
            "price_cents": 9900,
            "currency": "USD",
            "billing_period": "30_days_one_time",
            "max_documents": 10,
            "max_batteries": 3,
            "is_active": True,
        },
        "limits": {
            **_INHERITED_LIMITS,
            "display_order": ("int", 1),
            "scope": ("str", "company"),
            "trial_days": ("int", 30),
            "refund_guarantee_days": ("int", 30),
            "companies_max": ("int", 1),
            "employees_max": ("int", 5),
            "processes_max": ("int", 3),
            "learning_paths_max": ("int", 2),
            "progress_reports_max": ("int", 1),
            "onboarding_sessions": ("int", 1),
            "can_invite": ("bool", True),
            "can_version_documents": ("bool", True),
            "can_use_ai_generation": ("bool", True),
            "can_create_learning_paths": ("bool", True),
            "can_view_progress": ("bool", True),
            "can_issue_certificates": ("bool", True),
            "can_manage_roles": ("bool", True),
        },
    },
    {
        "tier": "business",
        "fields": {
            "name": "Ankard Business",
            "description": "Suscripción mensual para continuar después del piloto.",
            "price_cents": 14900,
            "currency": "USD",
            "billing_period": "monthly",
            "max_documents": None,
            "max_batteries": None,
            "is_active": True,
        },
        "limits": {
            **_INHERITED_LIMITS,
            "display_order": ("int", 2),
            "scope": ("str", "company"),
            "trial_days": ("int", 0),
            "refund_guarantee_days": ("int", 0),
            "companies_max": ("int", 1),
            "employees_max": ("int", 50),
            "processes_max": ("int", UNLIMITED),
            "learning_paths_max": ("int", UNLIMITED),
            "progress_reports_max": ("int", UNLIMITED),
            "can_invite": ("bool", True),
            "can_version_documents": ("bool", True),
            "can_use_ai_generation": ("bool", True),
            "can_create_learning_paths": ("bool", True),
            "can_view_progress": ("bool", True),
            "can_issue_certificates": ("bool", True),
            "can_manage_roles": ("bool", True),
        },
    },
]


def _limit_values(value_type, value):
    return {
        "value_type": value_type,
        "int_value": value if value_type == "int" else None,
        "bool_value": value if value_type == "bool" else None,
        "str_value": value if value_type == "str" else "",
    }


def _upsert(model, lookup, values):
    """Create or update one row; returns (obj, 'CREATED' | 'UPDATED' | 'UNCHANGED')."""
    obj = model.objects.filter(**lookup).first()
    if obj is None:
        return model.objects.create(**lookup, **values), "CREATED"
    changed = [k for k, v in values.items() if getattr(obj, k) != v]
    if not changed:
        return obj, "UNCHANGED"
    for k in changed:
        setattr(obj, k, values[k])
    obj.save(update_fields=changed)
    return obj, "UPDATED"


def seed_business_plans(log=None):
    """Upserts BUSINESS_PLANS; returns {'CREATED': n, 'UPDATED': n, 'UNCHANGED': n}."""
    log = log or (lambda status, label: None)
    counts = {"CREATED": 0, "UPDATED": 0, "UNCHANGED": 0}
    for spec in BUSINESS_PLANS:
        if spec["tier"] in PROTECTED_TIERS:
            raise CommandError(f"Refusing to touch protected tier {spec['tier']!r}.")
        plan, status = _upsert(Plan, {"tier": spec["tier"]}, spec["fields"])
        counts[status] += 1
        log(status, f"plan {plan.tier}")
        for key, (value_type, value) in spec["limits"].items():
            _, status = _upsert(PlanLimit, {"plan": plan, "key": key}, _limit_values(value_type, value))
            counts[status] += 1
            log(status, f"  {plan.tier}:{key} = {value}")
    return counts


class Command(BaseCommand):
    help = "Seed the Ankard Business Pilot / Ankard Business plans and their limits (additive, idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving.")
        parser.add_argument("--verbose", action="store_true", help="Also list UNCHANGED rows.")

    def handle(self, *args, dry_run=False, verbose=False, **options):
        styles = {"CREATED": self.style.SUCCESS, "UPDATED": self.style.WARNING, "UNCHANGED": str}

        def log(status, label):
            if status != "UNCHANGED" or verbose:
                self.stdout.write(styles[status](f"{status:<9} {label}"))

        with transaction.atomic():
            counts = seed_business_plans(log)
            if dry_run:
                transaction.set_rollback(True)

        summary = ", ".join(f"{k.lower()}={v}" for k, v in counts.items())
        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — rolled back, nothing saved ({summary})."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done ({summary})."))
