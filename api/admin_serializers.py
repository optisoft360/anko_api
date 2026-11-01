"""Read-only `*_detail` additions for the platform admin listings.

They extend the existing serializers, so every field the admin pages already read stays as is.
The views select/prefetch what these read, so a page costs a fixed number of queries.
"""
from rest_framework import serializers

from api.admin_listing import user_detail
from api.models import Battery, SupportRequest
from api.serializers import DeckListSerializer, FlashcardSerializer, PlanLimitSerializer, ProjectSerializer, SubscriptionSerializer


class SupportRequestAdminSerializer(serializers.ModelSerializer):
    """Admin inbox row: everything the public serializer shows plus status/assignee/note, editable."""

    user_detail = serializers.SerializerMethodField()
    assigned_to_detail = serializers.SerializerMethodField()

    class Meta:
        model = SupportRequest
        fields = ["id", "user", "user_detail", "name", "phone", "email", "message", "status", "source",
                  "created_at", "resolved_at", "assigned_to", "assigned_to_detail", "internal_note", "updated_at"]
        read_only_fields = ["id", "user", "name", "phone", "email", "message", "source", "created_at", "resolved_at", "updated_at"]

    def get_user_detail(self, obj):
        return user_detail(obj.user)

    def get_assigned_to_detail(self, obj):
        return user_detail(obj.assigned_to)

    def validate_assigned_to(self, value):
        from api.permissions import IsPlatformAdmin

        class _R:
            user = value
        if value is not None and not IsPlatformAdmin().has_permission(_R(), None):
            raise serializers.ValidationError("Only platform admins can be assigned.")
        return value

    def update(self, instance, validated_data):
        from django.utils import timezone
        status = validated_data.get("status")
        if status == SupportRequest.STATUS_DONE and instance.status != SupportRequest.STATUS_DONE:
            validated_data["resolved_at"] = timezone.now()
        elif status and status != SupportRequest.STATUS_DONE:
            validated_data["resolved_at"] = None
        return super().update(instance, validated_data)


def plan_detail(plan):
    return {"id": plan.id, "name": plan.name, "tier": plan.tier} if plan else None


def project_detail(project):
    return {"id": project.id, "title": project.title} if project else None


def _company_info(serializer, obj):
    return (serializer.context.get("company_map") or {}).get(obj.id) or {}


class AdminDeckSerializer(DeckListSerializer):
    owner_detail = serializers.SerializerMethodField()
    project_detail = serializers.SerializerMethodField()
    cards_count = serializers.SerializerMethodField()
    company_detail = serializers.SerializerMethodField()

    class Meta(DeckListSerializer.Meta):
        fields = list(DeckListSerializer.Meta.fields) + ["owner_detail", "project_detail", "cards_count", "company_detail"]

    def get_company_detail(self, obj):
        return _company_info(self, obj).get("company")

    def get_owner_detail(self, obj):
        return user_detail(obj.owner)

    def get_project_detail(self, obj):
        return project_detail(obj.project)

    def get_cards_count(self, obj):
        return self.get_cardsCount(obj)

    # The admin listing annotates/prefetches these; fall back to the base per-row queries otherwise.
    def get_approved_count(self, obj):
        n = getattr(obj, "approved_total", None)
        return n if n is not None else super().get_approved_count(obj)

    def get_rejected_count(self, obj):
        n = getattr(obj, "rejected_total", None)
        return n if n is not None else super().get_rejected_count(obj)

    def get_tag_group_id(self, obj):
        cache = getattr(obj, "_prefetched_objects_cache", {})
        if "source_tag_groups" in cache:
            first = next(iter(obj.source_tag_groups.all()), None)
            return first.tag_group_id if first else None
        return super().get_tag_group_id(obj)


class AdminProjectSerializer(ProjectSerializer):
    owner_detail = serializers.SerializerMethodField()

    class Meta(ProjectSerializer.Meta):
        fields = list(ProjectSerializer.Meta.fields) + ["owner_detail"]

    def get_owner_detail(self, obj):
        return user_detail(obj.owner)

    def get_documents_count(self, obj):
        annotated = getattr(obj, "documents_total", None)
        return annotated if annotated is not None else super().get_documents_count(obj)


class AdminFlashcardSerializer(FlashcardSerializer):
    deck_detail = serializers.SerializerMethodField()

    class Meta(FlashcardSerializer.Meta):
        fields = list(FlashcardSerializer.Meta.fields) + ["deck_detail"]

    def get_deck_detail(self, obj):
        return {"id": obj.deck.id, "title": obj.deck.title} if obj.deck_id else None


class AdminBatterySerializer(serializers.ModelSerializer):
    """Lean admin row: no per-user attempt data (that is what made the shared list heavy)."""

    question_count = serializers.IntegerField(read_only=True)
    project_detail = serializers.SerializerMethodField()
    rule_detail = serializers.SerializerMethodField()
    owner_detail = serializers.SerializerMethodField()
    company_detail = serializers.SerializerMethodField()

    class Meta:
        model = Battery
        fields = ["id", "name", "status", "difficulty", "visibility", "created_at", "project", "rule", "collection",
                  "question_count", "project_detail", "rule_detail", "owner_detail", "company_detail"]
        read_only_fields = fields

    def get_project_detail(self, obj):
        return project_detail(obj.project)

    def get_rule_detail(self, obj):
        return {"id": obj.rule.id, "name": obj.rule.name} if obj.rule_id else None

    def get_owner_detail(self, obj):
        # Batteries have no owner field: the project's owner, else whoever created its company process.
        if obj.project_id:
            return user_detail(obj.project.owner)
        return _company_info(self, obj).get("process_owner")

    def get_company_detail(self, obj):
        return _company_info(self, obj).get("company")


class SubscriptionAdminSerializer(SubscriptionSerializer):
    user_detail = serializers.SerializerMethodField()
    plan_detail = serializers.SerializerMethodField()

    class Meta(SubscriptionSerializer.Meta):
        fields = list(SubscriptionSerializer.Meta.fields) + ["user_detail", "plan_detail"]

    def get_user_detail(self, obj):
        return user_detail(obj.user)

    def get_plan_detail(self, obj):
        return plan_detail(obj.plan)


class PlanLimitAdminSerializer(PlanLimitSerializer):
    plan_detail = serializers.SerializerMethodField()

    class Meta(PlanLimitSerializer.Meta):
        fields = list(PlanLimitSerializer.Meta.fields) + ["plan_detail"]

    def get_plan_detail(self, obj):
        return plan_detail(obj.plan)


def get_admin_learning_path_serializer():
    # Imported lazily: api.enterprise serializers import api.serializers.
    from api.enterprise.serializers.learning import LearningPathListSerializer

    class _AdminLearningPathSerializer(LearningPathListSerializer):
        company_detail = serializers.SerializerMethodField()
        modules_count = serializers.SerializerMethodField()

        class Meta(LearningPathListSerializer.Meta):
            fields = list(LearningPathListSerializer.Meta.fields) + ["company_detail", "modules_count"]

        def get_company_detail(self, obj):
            return {"id": obj.company.id, "name": obj.company.name} if obj.company_id else None

        def get_module_count(self, obj):
            n = getattr(obj, "modules_total", None)
            return n if n is not None else super().get_module_count(obj)

        def get_modules_count(self, obj):
            return self.get_module_count(obj)

        def get_assignment_count(self, obj):
            n = getattr(obj, "assignments_total", None)
            return n if n is not None else super().get_assignment_count(obj)

    return _AdminLearningPathSerializer
