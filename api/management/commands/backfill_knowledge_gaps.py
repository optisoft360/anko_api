"""
Creates the knowledge gaps of existing finished Simulate attempts
(see api/enterprise/services/knowledge_gap_service.py).

    python manage.py backfill_knowledge_gaps --company 2
    python manage.py backfill_knowledge_gaps --company 2 --dry-run

Idempotent: re-running only updates what changed. --dry-run rolls everything back.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.enterprise.services.knowledge_gap_service import detect_knowledge_gaps
from api.enterprise_models import Company


class Command(BaseCommand):
    help = "Detect knowledge gaps from every finished Simulate attempt of a company."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, required=True, help="Company id.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving.")

    def handle(self, *args, company=None, dry_run=False, **options):
        if not Company.objects.filter(pk=company).exists():
            raise CommandError(f"Company {company} not found.")

        class _Rollback(Exception):
            pass

        try:
            with transaction.atomic():
                result = detect_knowledge_gaps(company)
                if dry_run:
                    raise _Rollback
        except _Rollback:
            pass

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}company {company}: {result['attempts']} attempts analyzed — "
            f"{result['created']} created, {result['updated']} updated, {result['reopened']} reopened, "
            f"{result['resolved']} resolved, {result['unchanged']} unchanged"
        ))
