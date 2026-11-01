"""
Knowledge gaps detected from Simulate (battery) results.

For every company member and process topic (TagGroup), the latest finished attempt
of each battery in that topic is scored together. Below 85 % correct the user has a
gap on that topic:

    critical < 40 %   high 40–59 %   medium 60–74 %   low 75–84 %

detect_knowledge_gaps() is idempotent: it keeps one "detected" gap per user + topic,
updates it while it is open/acknowledged, resolves it automatically once the user
reaches 85 %, and only reopens a resolved/dismissed gap when a newer attempt falls
below the threshold again. It runs after every finished attempt (see
BatteryViewSet.finish_attempt) and from `manage.py backfill_knowledge_gaps`.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# (upper bound, severity): the first bound the percent is below wins.
SEVERITY_BOUNDS = [
    (Decimal("40"), "critical"),
    (Decimal("60"), "high"),
    (Decimal("75"), "medium"),
    (Decimal("85"), "low"),
]
ACTIVE_STATUSES = ("open", "acknowledged")


def severity_for(percent: Decimal) -> str | None:
    """Severity for a topic score (0-100), or None when there is no gap."""
    for bound, severity in SEVERITY_BOUNDS:
        if percent < bound:
            return severity
    return None


def _battery_knowledge_source(battery):
    """The KnowledgeSource (process) a battery belongs to, or None for personal/project
    batteries: its topic's KnowledgeSourceTopic, then its source documents, then the
    tag-group/run path used by the retention hook."""
    from api.enterprise_document_intelligence_models import KnowledgeSourceDocument, KnowledgeSourceTopic
    from api.models import BatterySourceDocument, BatterySourceTagGroup

    topic = (
        KnowledgeSourceTopic.objects.filter(tag_group_id__in=BatterySourceTagGroup.objects.filter(battery=battery).values("tag_group_id"))
        .select_related("knowledge_source").first()
    )
    if topic:
        return topic.knowledge_source
    doc_ids = BatterySourceDocument.objects.filter(battery=battery).values_list("document_id", flat=True)
    link = (
        KnowledgeSourceDocument.objects.filter(document_id__in=doc_ids)
        .select_related("knowledge_source").first()
    )
    if link:
        return link.knowledge_source
    from api.views import _resolve_ks_for_battery
    return _resolve_ks_for_battery(battery)


def _battery_topics(battery):
    """{question_id: ("tag_group" | "topic", id)} for the questions of a battery.

    Process batteries belong to their TagGroup(s) as a whole; older batteries may
    carry a legacy Topic per question instead. Questions without either are skipped."""
    from api.models import BatterySourceTagGroup

    tag_group_ids = sorted(BatterySourceTagGroup.objects.filter(battery=battery).values_list("tag_group_id", flat=True).distinct())
    keys = {}
    for qid, topic_id in battery.questions_rel.values_list("id", "topic_id"):
        if tag_group_ids:
            keys[qid] = [("tag_group", tg) for tg in tag_group_ids]
        elif topic_id:
            keys[qid] = [("topic", topic_id)]
    return keys


def _company_batteries(company_id, battery_ids):
    """{battery_id: (battery, knowledge_source)} for the batteries of this company's processes."""
    from api.models import Battery

    out = {}
    for battery in Battery.objects.filter(id__in=battery_ids):
        try:
            ks = _battery_knowledge_source(battery)
        except Exception:
            logger.exception("knowledge gaps: could not resolve the process of battery %s", battery.id)
            continue
        if ks and str(ks.company_id) == str(company_id):
            out[battery.id] = (battery, ks)
    return out


def _analyzable_attempts(company_id, user_id=None):
    """Latest finished attempt with at least one answer, per (user, battery), for the
    company's batteries. Attempts finished without answering anything are ignored."""
    from api.enterprise_models import CompanyMembership
    from api.models import BatteryAttempt

    members = CompanyMembership.objects.filter(company_id=company_id, status="active")
    if user_id:
        members = members.filter(user_id=user_id)
    attempts = (
        BatteryAttempt.objects.filter(status="finished", user_id__in=members.values("user_id"), answers__isnull=False)
        .distinct().order_by("-finished_at", "-id")
    )
    latest = {}
    for attempt in attempts:
        latest.setdefault((attempt.user_id, attempt.battery_id), attempt)
    batteries = _company_batteries(company_id, {b for _, b in latest})
    return [(a, *batteries[a.battery_id]) for a in latest.values() if a.battery_id in batteries]


def count_analyzable_attempts(company_id, user_id=None) -> int:
    return len(_analyzable_attempts(company_id, user_id))


def _assignment_for(company_id, user_id, ks):
    from api.enterprise_learning_models import LearningPathAssignment

    return (
        LearningPathAssignment.objects.filter(company_id=company_id, user_id=user_id)
        .filter(Q(learning_module__knowledge_source=ks) | Q(learning_path__modules__knowledge_source=ks))
        .order_by("-created_at").first()
    )


def _pct(correct, total) -> Decimal:
    return (Decimal(correct) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@transaction.atomic
def detect_knowledge_gaps(company_id, user_id=None, battery_id=None) -> dict:
    """Create/update the topic gaps of a company (or one user / the topics of one battery).

    Returns counts: created, updated, reopened, resolved, unchanged, attempts.
    """
    from api.enterprise_models import LearningEvent
    from api.enterprise_retention_models import KnowledgeGap

    rows = _analyzable_attempts(company_id, user_id)
    only_keys = None  # with battery_id: only the topics of that battery
    if battery_id:
        only_keys = set()
        for _, battery, _ in rows:
            if battery.id == int(battery_id):
                for keys in _battery_topics(battery).values():
                    only_keys.update(keys)

    # (user, topic key) -> totals over the latest attempt of each battery in the topic
    stats = defaultdict(lambda: {"total": 0, "correct": 0, "battery": None, "ks": None, "attempt": None})
    for attempt, battery, ks in rows:
        answers = dict(attempt.answers.values_list("question_id", "is_correct"))
        for qid, keys in _battery_topics(battery).items():
            for key in keys:
                if only_keys is not None and key not in only_keys:
                    continue
                s = stats[(attempt.user_id, key)]
                s["total"] += 1
                s["correct"] += 1 if answers.get(qid) else 0
                if s["attempt"] is None or attempt.finished_at > s["attempt"].finished_at:
                    s.update(battery=battery, ks=ks, attempt=attempt)

    result = {"created": 0, "updated": 0, "reopened": 0, "resolved": 0, "unchanged": 0, "attempts": len(rows)}
    now = timezone.now()
    for (uid, (kind, key_id)), s in stats.items():
        if not s["total"]:
            continue
        percent = _pct(s["correct"], s["total"])
        severity = severity_for(percent)
        attempt = s["attempt"]
        target = {"tag_group_id": key_id} if kind == "tag_group" else {"topic_id": key_id, "tag_group__isnull": True}
        gap = (
            KnowledgeGap.objects.select_for_update()
            .filter(company_id=company_id, user_id=uid, source=KnowledgeGap.SOURCE_DETECTED, **target)
            .order_by("-detected_at").first()
        )
        assignment = _assignment_for(company_id, uid, s["ks"])
        values = {
            "correct_percent": percent,
            "retention_score_at_detection": percent,
            "questions_total": s["total"],
            "questions_missed": s["total"] - s["correct"],
            "battery": s["battery"],
            "learning_assignment": assignment,
            "learning_path_id": assignment.learning_path_id if assignment else None,
        }
        metadata = {"last_attempt_id": attempt.id, "last_attempt_at": attempt.finished_at.isoformat(), "knowledge_source_id": s["ks"].id}

        if severity is None:
            if gap and gap.status in ACTIVE_STATUSES:
                for k, v in values.items():
                    setattr(gap, k, v)
                gap.status = "resolved"
                gap.resolved_at = now
                gap.resolved_by = None
                gap.notes = gap.notes or f"Auto-resolved: {percent}% correct in the latest Simulate."
                gap.metadata = {**gap.metadata, **metadata}
                gap.save()
                result["resolved"] += 1
            else:
                result["unchanged"] += 1
            continue

        if gap is None:
            gap = KnowledgeGap.objects.create(
                company_id=company_id, user_id=uid, source=KnowledgeGap.SOURCE_DETECTED,
                severity=severity, status="open", detected_at=attempt.finished_at, metadata=metadata,
                **({"tag_group_id": key_id} if kind == "tag_group" else {"topic_id": key_id}), **values,
            )
            LearningEvent.objects.create(
                company_id=company_id, user_id=uid, event_type="knowledge_gap_detected",
                metadata={"gap_id": gap.id, "correct_percent": str(percent), "severity": severity},
            )
            result["created"] += 1
            continue

        if gap.status in ACTIVE_STATUSES:
            changed = gap.severity != severity or gap.correct_percent != percent or gap.metadata.get("last_attempt_id") != attempt.id
            for k, v in values.items():
                setattr(gap, k, v)
            gap.severity = severity
            gap.metadata = {**gap.metadata, **metadata}
            gap.save()
            result["updated" if changed else "unchanged"] += 1
            continue

        # resolved / dismissed: reopen only for an attempt finished after it was closed
        closed_at = gap.resolved_at or gap.updated_at
        if attempt.finished_at > closed_at:
            for k, v in values.items():
                setattr(gap, k, v)
            gap.severity = severity
            gap.status = "open"
            gap.detected_at = attempt.finished_at
            gap.acknowledged_at = gap.acknowledged_by = gap.resolved_at = gap.resolved_by = None
            gap.metadata = {**gap.metadata, **metadata, "reopened_at": now.isoformat()}
            gap.save()
            result["reopened"] += 1
        else:
            result["unchanged"] += 1
    return result


def detect_after_attempt(attempt) -> None:
    """Hook for a finished attempt: refresh that user's gaps for the battery's topics.
    Never raises — finishing a Simulate must not fail because of this."""
    try:
        ks = _battery_knowledge_source(attempt.battery)
        if ks:
            detect_knowledge_gaps(ks.company_id, user_id=attempt.user_id, battery_id=attempt.battery_id)
    except Exception:
        logger.exception("knowledge gaps: detection failed for attempt %s", attempt.id)
