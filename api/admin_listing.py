"""Optional ?search=, ?ordering= and filter params for the platform admin listings.

Every param is optional and whitelisted per listing: without params the queryset (and so the
response) is exactly the same as before.
"""
from __future__ import annotations

from django.db.models import Q

TRUE = {"1", "true", "yes"}
FALSE = {"0", "false", "no"}


def apply_list_params(request, qs, *, search=(), ordering=(), filters=None):
    """
    search:   field lookups OR-ed with icontains for ?search=
    ordering: allowed field names for ?ordering= (a leading "-" reverses); anything else is ignored
    filters:  {param: (lookup, kind)} with kind "int", "bool" or "str"; invalid values are ignored
    """
    params = request.query_params
    term = (params.get("search") or "").strip()
    if term and search:
        q = Q()
        for field in search:
            q |= Q(**{f"{field}__icontains": term})
        qs = qs.filter(q)

    for param, (lookup, kind) in (filters or {}).items():
        raw = (params.get(param) or "").strip()
        if raw == "":
            continue
        if kind == "int":
            if not raw.isdigit():
                continue
            value = int(raw)
        elif kind == "bool":
            low = raw.lower()
            if low not in TRUE | FALSE:
                continue
            value = low in TRUE
        else:
            value = raw
        qs = qs.filter(**{lookup: value})

    if search or filters:
        qs = qs.distinct()

    order = (params.get("ordering") or "").strip()
    if order and order.lstrip("-") in set(ordering):
        qs = qs.order_by(order, "-id" if order.lstrip("-") != "id" else order)
    return qs


def user_detail(user):
    return {"id": user.id, "username": user.username, "email": user.email} if user else None


def company_content_map(kind, ids):
    """
    {deck_or_battery_id: {"company": {id, name} | None, "process_owner": user_detail | None}} for one page.

    Company content has no Project: it reaches its company through its topic (hand-made
    KnowledgeSourceTopic or an Auto-generate run over the process's documents) or a learning module.
    Same rules as api.services.content_access.content_company_ids, batched: six queries per page.
    """
    from api.enterprise_document_intelligence_models import KnowledgeSource, KnowledgeSourceDocument, KnowledgeSourceTopic
    from api.enterprise_learning_models import LearningModuleItem
    from api.models import BatterySourceTagGroup, DeckSourceTagGroup, ProcessArtifact

    ids = [int(i) for i in ids]
    if not ids:
        return {}
    link_model = DeckSourceTagGroup if kind == "deck" else BatterySourceTagGroup
    obj_tgs = {}
    for oid, tg in link_model.objects.filter(**{f"{kind}_id__in": ids}).values_list(f"{kind}_id", "tag_group_id"):
        obj_tgs.setdefault(oid, set()).add(tg)
    all_tgs = {tg for tgs in obj_tgs.values() for tg in tgs}

    tg_ks = {}
    for tg, ks in KnowledgeSourceTopic.objects.filter(tag_group_id__in=all_tgs).values_list("tag_group_id", "knowledge_source_id"):
        tg_ks.setdefault(tg, set()).add(ks)

    artifacts = list(ProcessArtifact.objects.filter(
        Q(artifact_type=kind, resource_id__in=[str(i) for i in ids])
        | Q(artifact_type="tag_group", resource_id__in=[str(t) for t in all_tgs]),
        run__workflow_key="collection_auto_generate", run__resource_type="document_batch",
    ).values_list("artifact_type", "resource_id", "run__resource_id"))

    def doc_ids(run_resource):
        try:
            return {int(v) for v in str(run_resource or "").split(",") if v.strip()}
        except ValueError:
            return set()

    run_docs = {r: doc_ids(r) for _, _, r in artifacts}
    doc_ks = {}
    for doc, ks in KnowledgeSourceDocument.objects.filter(
        document_id__in={d for docs in run_docs.values() for d in docs}
    ).values_list("document_id", "knowledge_source_id"):
        doc_ks.setdefault(doc, set()).add(ks)
    ks_of_run = {r: {k for d in docs for k in doc_ks.get(d, ())} for r, docs in run_docs.items()}

    obj_ks = {oid: set() for oid in ids}
    for oid, tgs in obj_tgs.items():
        for tg in tgs:
            obj_ks[oid] |= tg_ks.get(tg, set())
    for art_type, resource_id, run in artifacts:
        rid = int(resource_id)
        if art_type == kind and rid in obj_ks:
            obj_ks[rid] |= ks_of_run.get(run, set())
        elif art_type == "tag_group":
            for oid, tgs in obj_tgs.items():
                if rid in tgs:
                    obj_ks[oid] |= ks_of_run.get(run, set())

    ks_rows = {ks.id: ks for ks in KnowledgeSource.objects.filter(
        id__in={k for s in obj_ks.values() for k in s}).select_related("company", "created_by")}
    module_company = {}
    for oid, cid, cname in LearningModuleItem.objects.filter(**{f"{kind}_id__in": ids}).values_list(
            f"{kind}_id", "module__company_id", "module__company__name"):
        if cid:
            module_company.setdefault(oid, {"id": cid, "name": cname})

    out = {}
    for oid in ids:
        sources = sorted((ks_rows[k] for k in obj_ks[oid] if k in ks_rows), key=lambda ks: ks.id)
        ks = sources[0] if sources else None
        company = {"id": ks.company_id, "name": ks.company.name} if ks and ks.company_id else module_company.get(oid)
        out[oid] = {"company": company, "process_owner": user_detail(ks.created_by) if ks else None}
    return out
