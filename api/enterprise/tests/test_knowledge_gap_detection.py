"""Knowledge gaps detected per topic from finished Simulate attempts."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from api.enterprise.services.knowledge_gap_service import detect_knowledge_gaps, severity_for
from api.enterprise_document_intelligence_models import KnowledgeSource, KnowledgeSourceDocument, KnowledgeSourceTopic
from api.enterprise_models import Company, CompanyMembership
from api.enterprise_retention_models import KnowledgeGap
from api.models import (
    Battery, BatteryAttempt, BatteryAttemptAnswer, BatteryOption, BatteryQuestion,
    BatterySourceDocument, BatterySourceTagGroup, Document, Project, TagGroup,
)

User = get_user_model()


class GapFixture:
    """A company with one process, one topic and a 10-question battery."""

    def make_fixture(self):
        self.owner = User.objects.create_user("gap_owner", "gap_owner@test.com", "Pass123!")
        self.manager = User.objects.create_user("gap_manager", "gap_manager@test.com", "Pass123!")
        self.employee = User.objects.create_user("gap_emp", "gap_emp@test.com", "Pass123!")
        self.other = User.objects.create_user("gap_emp2", "gap_emp2@test.com", "Pass123!")
        self.company = Company.objects.create(name="Gap Co", slug="gap-co", owner=self.owner)
        for user, role in ((self.owner, "owner"), (self.manager, "manager"), (self.employee, "employee"), (self.other, "employee")):
            CompanyMembership.objects.get_or_create(company=self.company, user=user, defaults={"role": role, "status": "active"})
        doc = Document.objects.create(filename="manual.pdf", type="pdf", size=1, hash="gap", uploaded_by=self.owner, status="ready")
        self.ks = KnowledgeSource.objects.create(company=self.company, document=doc, title="Forklift", source_type="manual",
                                                 status="processed", created_by=self.owner)
        KnowledgeSourceDocument.objects.create(knowledge_source=self.ks, document=doc)
        self.topic = TagGroup.objects.create(name="forklift safety brakes load")
        KnowledgeSourceTopic.objects.create(knowledge_source=self.ks, tag_group=self.topic, title="Forklift safety", created_by=self.owner)
        self.battery = self.make_battery(doc, self.topic)

    def make_battery(self, doc, topic, n=10):
        battery = Battery.objects.create(name=f"Quiz {topic.name}", status="Ready", difficulty="Medium")
        BatterySourceDocument.objects.create(battery=battery, document=doc)
        BatterySourceTagGroup.objects.create(battery=battery, tag_group=topic)
        for i in range(n):
            q = BatteryQuestion.objects.create(battery=battery, type="singleChoice", question=f"Q{i}", points=1, order=i)
            BatteryOption.objects.create(question=q, option_id="a", text="right", correct=True)
            BatteryOption.objects.create(question=q, option_id="b", text="wrong", correct=False)
        return battery

    def attempt(self, user, correct, battery=None, when=None, answered=None):
        """A finished attempt with `correct` right answers (of `answered`, default all)."""
        battery = battery or self.battery
        questions = list(battery.questions_rel.order_by("order"))
        a = BatteryAttempt.objects.create(battery=battery, user=user, status="finished", total_questions=len(questions),
                                          correct_count=correct, finished_at=when or timezone.now())
        for i, q in enumerate(questions[: answered if answered is not None else len(questions)]):
            BatteryAttemptAnswer.objects.create(attempt=a, question=q, is_correct=i < correct)
        return a

    def gaps(self, **kw):
        return KnowledgeGap.objects.filter(company=self.company, source="detected", **kw)


class SeverityTests(TestCase):
    def test_bounds(self):
        cases = {"0": "critical", "39.99": "critical", "40": "high", "59.99": "high", "60": "medium",
                 "74.99": "medium", "75": "low", "84.99": "low", "85": None, "100": None}
        for pct, expected in cases.items():
            self.assertEqual(severity_for(Decimal(pct)), expected, pct)


class DetectionTests(GapFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def test_creates_one_gap_per_user_and_topic(self):
        self.attempt(self.employee, correct=3)   # 30 % -> critical
        self.attempt(self.other, correct=7)      # 70 % -> medium
        result = detect_knowledge_gaps(self.company.id)
        self.assertEqual(result["created"], 2)
        gap = self.gaps().get(user=self.employee)
        self.assertEqual((gap.severity, gap.status, gap.tag_group, gap.battery), ("critical", "open", self.topic, self.battery))
        self.assertEqual((gap.correct_percent, gap.questions_total, gap.questions_missed), (Decimal("30.00"), 10, 7))
        self.assertEqual(self.gaps().get(user=self.other).severity, "medium")

    def test_no_gap_at_85_and_ignores_unanswered_and_personal_batteries(self):
        self.attempt(self.employee, correct=9)                  # 90 %
        self.attempt(self.other, correct=0, answered=0)         # finished without answering
        personal = Battery.objects.create(project=Project.objects.create(title="P", owner=self.employee),
                                          name="Mine", status="Ready", difficulty="Medium")
        q = BatteryQuestion.objects.create(battery=personal, type="singleChoice", question="x", points=1)
        a = BatteryAttempt.objects.create(battery=personal, user=self.employee, status="finished", finished_at=timezone.now())
        BatteryAttemptAnswer.objects.create(attempt=a, question=q, is_correct=False)
        detect_knowledge_gaps(self.company.id)
        self.assertFalse(KnowledgeGap.objects.exists())

    def test_idempotent(self):
        self.attempt(self.employee, correct=5)
        detect_knowledge_gaps(self.company.id)
        again = detect_knowledge_gaps(self.company.id)
        self.assertEqual((again["created"], again["unchanged"]), (0, 1))
        self.assertEqual(self.gaps().count(), 1)

    def test_acknowledged_gap_is_updated_not_duplicated(self):
        self.attempt(self.employee, correct=5, when=timezone.now() - timedelta(days=2))
        detect_knowledge_gaps(self.company.id)
        self.gaps().update(status="acknowledged")
        self.attempt(self.employee, correct=2)  # worse
        detect_knowledge_gaps(self.company.id)
        gap = self.gaps().get()
        self.assertEqual((gap.status, gap.severity, gap.correct_percent), ("acknowledged", "critical", Decimal("20.00")))

    def test_resolved_gap_is_not_reopened_without_a_new_attempt(self):
        self.attempt(self.employee, correct=5, when=timezone.now() - timedelta(days=3))
        detect_knowledge_gaps(self.company.id)
        self.gaps().update(status="resolved", resolved_at=timezone.now() - timedelta(days=1))
        detect_knowledge_gaps(self.company.id)
        self.assertEqual(self.gaps().get().status, "resolved")
        # a newer attempt still below the threshold reopens it
        self.attempt(self.employee, correct=4)
        result = detect_knowledge_gaps(self.company.id)
        self.assertEqual(result["reopened"], 1)
        gap = self.gaps().get()
        self.assertEqual((gap.status, gap.resolved_at), ("open", None))

    def test_passing_the_latest_attempt_resolves_the_open_gap(self):
        self.attempt(self.employee, correct=5, when=timezone.now() - timedelta(days=1))
        detect_knowledge_gaps(self.company.id)
        self.attempt(self.employee, correct=10)
        result = detect_knowledge_gaps(self.company.id)
        self.assertEqual(result["resolved"], 1)
        self.assertEqual(self.gaps().get().status, "resolved")

    def test_topic_score_combines_the_latest_attempt_of_each_battery(self):
        second = self.make_battery(self.ks.document, self.topic)
        self.attempt(self.employee, correct=10)                 # 10/10
        self.attempt(self.employee, correct=4, battery=second)  # 4/10 -> topic 14/20 = 70 %
        detect_knowledge_gaps(self.company.id)
        gap = self.gaps().get()
        self.assertEqual((gap.correct_percent, gap.severity, gap.questions_total), (Decimal("70.00"), "medium", 20))

    def test_other_company_attempts_do_not_create_gaps_here(self):
        other_co = Company.objects.create(name="Other", slug="other-co", owner=self.owner)
        self.attempt(self.employee, correct=1)
        detect_knowledge_gaps(other_co.id)
        self.assertFalse(KnowledgeGap.objects.filter(company=other_co).exists())

    def test_backfill_command(self):
        self.attempt(self.employee, correct=3)
        out = StringIO()
        call_command("backfill_knowledge_gaps", company=self.company.id, stdout=out)
        self.assertIn("1 created", out.getvalue())
        call_command("backfill_knowledge_gaps", company=self.company.id, dry_run=True, stdout=out)
        self.assertEqual(self.gaps().count(), 1)

    def test_seed_command_only_touches_demo_rows(self):
        self.attempt(self.employee, correct=3)
        detect_knowledge_gaps(self.company.id)
        out = StringIO()
        call_command("seed_knowledge_gaps", company=self.company.id, count=4, stdout=out)
        seeded = KnowledgeGap.objects.filter(source="seed")
        self.assertEqual(seeded.count(), 4)
        self.assertEqual(set(seeded.values_list("severity", flat=True)), {"critical", "high", "medium", "low"})
        self.assertTrue(all(g.tag_group_id == self.topic.id for g in seeded))
        call_command("seed_knowledge_gaps", company=self.company.id, clear_only=True, stdout=out)
        self.assertFalse(KnowledgeGap.objects.filter(source="seed").exists())
        self.assertEqual(self.gaps().count(), 1)


class GapApiTests(GapFixture, APITestCase):
    def setUp(self):
        call_command("seed_routes_rbac", verbosity=0)
        self.make_fixture()

    def url(self, suffix=""):
        return f"/api/enterprise/knowledge-gaps/{suffix}"

    def test_finishing_an_attempt_with_misses_creates_the_gap(self):
        self.client.force_authenticate(self.employee)
        start = self.client.post(f"/api/batteries/{self.battery.id}/start_attempt/", {}, format="json")
        self.assertEqual(start.status_code, 201, start.content)
        attempt_id = start.data["id"]
        for i, q in enumerate(self.battery.questions_rel.order_by("order")):
            option = q.options.get(correct=i < 2)
            r = self.client.post(f"/api/batteries/{self.battery.id}/answer/",
                                 {"attempt_id": attempt_id, "question_id": q.id, "selected_option_id": option.id}, format="json")
            self.assertIn(r.status_code, (200, 201), r.content)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(f"/api/batteries/{self.battery.id}/finish_attempt/",
                                 {"attempt_id": attempt_id, "total_score": 2, "max_score": 10, "correct_count": 2, "total_questions": 10}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        gap = self.gaps().get(user=self.employee)
        self.assertEqual((gap.severity, gap.correct_percent, gap.questions_missed), ("critical", Decimal("20.00"), 8))

    def test_filters_scope_and_actions(self):
        self.attempt(self.employee, correct=3)
        self.attempt(self.other, correct=7)
        detect_knowledge_gaps(self.company.id)

        self.client.force_authenticate(self.manager)
        r = self.client.get(self.url(), {"company_id": self.company.id})
        self.assertEqual(r.data["count"], 2)
        item = r.data["results"][0]
        self.assertEqual(item["topic_name"], "Forklift safety")
        r = self.client.get(self.url(), {"company_id": self.company.id, "severity": "critical"})
        self.assertEqual([g["user"] for g in r.data["results"]], [self.employee.id])

        gap = self.gaps().get(user=self.employee)
        r = self.client.post(self.url(f"{gap.id}/acknowledge/"), {"company_id": self.company.id})
        self.assertEqual(r.data["status"], "acknowledged")
        r = self.client.get(self.url(), {"company_id": self.company.id, "status": "acknowledged"})
        self.assertEqual([g["id"] for g in r.data["results"]], [gap.id])
        r = self.client.post(self.url(f"{gap.id}/resolve/"), {"company_id": self.company.id})
        self.assertEqual(r.data["status"], "resolved")

        # an employee only sees their own gaps
        self.client.force_authenticate(self.employee)
        r = self.client.get(self.url(), {"company_id": self.company.id})
        self.assertEqual({g["user"] for g in r.data["results"]}, {self.employee.id})

    def test_summary_counts_analyzable_attempts(self):
        self.client.force_authenticate(self.manager)
        r = self.client.get(self.url("summary/"), {"company_id": self.company.id})
        self.assertEqual(r.data, {"analyzable_attempts": 0, "gaps": 0})
        self.attempt(self.employee, correct=9)
        r = self.client.get(self.url("summary/"), {"company_id": self.company.id})
        self.assertEqual(r.data, {"analyzable_attempts": 1, "gaps": 0})
