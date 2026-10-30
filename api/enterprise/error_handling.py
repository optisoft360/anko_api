from __future__ import annotations

import logging

from rest_framework.exceptions import PermissionDenied, ValidationError

logger = logging.getLogger(__name__)


def log_public_exception(message: str, exc: Exception, **context) -> None:
    context = {key: value for key, value in context.items() if value is not None}
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    if context:
        suffix = " ".join(f"{key}={value}" for key, value in sorted(context.items()))
        logger.error("%s | %s", message, suffix, exc_info=exc_info)
        return
    logger.error("%s", message, exc_info=exc_info)


def company_access_denied(exc: Exception, *, user_id=None, company_id=None):
    log_public_exception(
        "Enterprise company access denied",
        exc,
        user_id=user_id,
        company_id=company_id,
    )
    return PermissionDenied("You do not have access to this company.")


def generic_permission_denied(
    exc: Exception,
    *,
    log_message: str = "Enterprise permission check failed",
    detail: str = "You do not have permission to perform this action.",
    **context,
):
    log_public_exception(log_message, exc, **context)
    return PermissionDenied(detail)


def generic_validation_error(
    exc: Exception,
    *,
    log_message: str = "Enterprise request failed",
    detail: str = "The request could not be completed.",
    field: str = "detail",
    **context,
):
    log_public_exception(log_message, exc, **context)
    return ValidationError({field: detail} if field else detail)
