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
