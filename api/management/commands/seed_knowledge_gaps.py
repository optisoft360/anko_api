"""
Demo knowledge gaps for trying the Knowledge Gaps screen, built only from users,
process topics (TagGroups), batteries and assignments that already exist.

    python manage.py seed_knowledge_gaps --company 2            # add demo gaps
    python manage.py seed_knowledge_gaps --company 2 --clear    # replace the demo gaps
    python manage.py seed_knowledge_gaps --company 2 --clear-only

Demo rows have source="seed": --clear only ever deletes those, never detected gaps.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.enterprise.services.knowledge_gap_service import SEVERITY_BOUNDS
from api.enterprise_document_intelligence_models import KnowledgeSource, KnowledgeSourceDocument
from api.enterprise_learning_models import LearningPathAssignment
from api.enterprise_models import Company, CompanyMembership
from api.enterprise_retention_models import KnowledgeGap
from api.models import BatterySourceDocument, BatterySourceTagGroup, DeckSourceDocument, DeckSourceTagGroup

# severity -> (lowest, highest) percent correct
BANDS = {"critical": (5, 39), "high": (40, 59), "medium": (60, 74), "low": (75, 84)}
assert [b[1] for b in SEVERITY_BOUNDS] == ["critical", "high", "medium", "low"]


class Command(BaseCommand):
    help = "Create demo knowledge gaps (source=seed) from a company's existing users and topics."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, required=True, help="Company id.")
        parser.add_argument("--count", type=int, default=18, help="How many demo gaps (default 18).")
        parser.add_argument("--clear", action="store_true", help="Delete the previous demo gaps first.")
        parser.add_argument("--clear-only", action="store_true", help="Only delete the demo gaps.")
        parser.add_argument("--seed", type=int, default=360, help="Random seed (same data every run).")

    @transaction.atomic
    def handle(self, *args, company=None, count=18, clear=False, clear_only=False, seed=360, **options):
        comp = Company.objects.filter(pk=company).first()
        if not comp:
            raise CommandError(f"Company {company} not found.")

        if clear or clear_only:
            deleted, _ = KnowledgeGap.objects.filter(company=comp, source=KnowledgeGap.SOURCE_SEED).delete()
            self.stdout.write(f"Deleted {deleted} demo gaps.")
            if clear_only:
                return

        users = list(
            CompanyMembership.objects.filter(company=comp, status="active")
            .select_related("user").order_by("user__username").values_list("user_id", flat=True)
        )
        # Topics = the TagGroups of this company's processes (through their batteries and decks).
        ks_ids = list(KnowledgeSource.objects.filter(company=comp).values_list("id", flat=True))
        doc_ids = KnowledgeSourceDocument.objects.filter(knowledge_source_id__in=ks_ids).values("document_id")
        battery_ids = BatterySourceDocument.objects.filter(document_id__in=doc_ids).values("battery_id")
        deck_ids = DeckSourceDocument.objects.filter(document_id__in=doc_ids).values("deck_id")
        battery_by_tg = {}
        for tg_id, b_id in BatterySourceTagGroup.objects.filter(battery_id__in=battery_ids).values_list("tag_group_id", "battery_id"):
            battery_by_tg.setdefault(tg_id, b_id)
        tag_groups = sorted(set(battery_by_tg) | set(
            DeckSourceTagGroup.objects.filter(deck_id__in=deck_ids).values_list("tag_group_id", flat=True)
        ))
        if not users or not tag_groups:
            raise CommandError(f"Company {company} needs active members ({len(users)}) and process topics ({len(tag_groups)}).")

        rng = random.Random(seed)
        existing = set(KnowledgeGap.objects.filter(company=comp, source=KnowledgeGap.SOURCE_SEED).values_list("user_id", "tag_group_id"))
        combos = [(u, tg) for u in users for tg in tag_groups if (u, tg) not in existing]
        rng.shuffle(combos)
        combos = combos[:count]
        if len(combos) < count:
            self.stdout.write(self.style.WARNING(f"Only {len(combos)} new user/topic pairs available."))

        severities = list(BANDS)
        now = timezone.now()
        created = {"open": 0, "acknowledged": 0, "resolved": 0}
        for i, (user_id, tg_id) in enumerate(combos):
            severity = severities[i % 4]
            status = "open" if i % 5 < 3 else ("acknowledged" if i % 5 == 3 else "resolved")
            percent = Decimal(rng.randint(*BANDS[severity]))
            total = rng.choice([10, 12, 15])
            missed = max(1, round(total * (100 - percent) / 100))
            detected = now - timedelta(days=rng.randint(0, 29), hours=rng.randint(0, 23))
            assignment = (
                LearningPathAssignment.objects.filter(company=comp, user_id=user_id)
                .filter(Q(learning_module__knowledge_source_id__in=ks_ids) | Q(learning_path__modules__knowledge_source_id__in=ks_ids))
                .order_by("-created_at").first()
            )
            gap = KnowledgeGap(
                company=comp, user_id=user_id, tag_group_id=tg_id, battery_id=battery_by_tg.get(tg_id),
                learning_assignment=assignment, learning_path_id=assignment.learning_path_id if assignment else None,
                source=KnowledgeGap.SOURCE_SEED, severity=severity, status=status,
                correct_percent=percent, retention_score_at_detection=percent,
                questions_total=total, questions_missed=missed, detected_at=detected,
                metadata={"demo": True},
            )
            if status in ("acknowledged", "resolved"):
                gap.acknowledged_at = min(now, detected + timedelta(hours=rng.randint(2, 48)))
            if status == "resolved":
                gap.resolved_at = min(now, gap.acknowledged_at + timedelta(days=rng.randint(1, 5)))
                gap.notes = "Demo: reviewed with the trainer."
            gap.save()
            created[status] += 1

        self.stdout.write(self.style.SUCCESS(
            f"company {company}: {sum(created.values())} demo gaps "
            f"({created['open']} open, {created['acknowledged']} acknowledged, {created['resolved']} resolved) "
            f"across {len({u for u, _ in combos})} users and {len({t for _, t in combos})} topics"
        ))
