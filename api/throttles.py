from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class BurstAnonRateThrottle(AnonRateThrottle):
    scope = "anon_burst"


class SustainedAnonRateThrottle(AnonRateThrottle):
    scope = "anon_sustained"


def _is_platform_admin(user):
    from api.permissions import IsPlatformAdmin

    class _Req:  # IsPlatformAdmin only reads request.user
        pass
    req = _Req()
    req.user = user
    return IsPlatformAdmin().has_permission(req, None)


class _StaffAwareUserRateThrottle(UserRateThrottle):
    """Platform admins get their own, higher scope (and a separate counter)."""
    staff_scope = None

    def allow_request(self, request, view):
        if self.staff_scope and _is_platform_admin(request.user):
            self.scope = self.staff_scope
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class BurstUserRateThrottle(_StaffAwareUserRateThrottle):
    scope = "user_burst"
    staff_scope = "staff_burst"


class SustainedUserRateThrottle(_StaffAwareUserRateThrottle):
    scope = "user_sustained"
    staff_scope = "staff_sustained"


class LeadAnonRateThrottle(AnonRateThrottle):
    """Caps anonymous lead submissions (public landing forms) per IP."""
    scope = "anon_lead"


# ---------------------------------------------------------------------------
# C3: scoped limits for sensitive / expensive endpoints. A per-view
# throttle_classes list replaces the global defaults, so these are always used
# together with them (see with_default_throttles).
# ---------------------------------------------------------------------------

class AuthAnonRateThrottle(AnonRateThrottle):
    """Login, registration, password reset and e-mail verification, per IP."""
    scope = "auth"


class InvitationUserRateThrottle(UserRateThrottle):
    """Sending / resending company invitations, per user."""
    scope = "invitations"


class AIGenerationUserRateThrottle(UserRateThrottle):
    """AI generation requests (decks, batteries, auto-generate, graph rebuild), per user."""
    scope = "ai_generation"


class VerifyAnonRateThrottle(AnonRateThrottle):
    """C6: public certificate verification (QR / Verify), per IP."""
    scope = "verify"


def with_default_throttles(*extra):
    from rest_framework.settings import api_settings
    return [*api_settings.DEFAULT_THROTTLE_CLASSES, *extra]
