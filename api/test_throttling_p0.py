"""429 handling: Retry-After header and the separate, higher scope for platform admins."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from api.models import User
from api.throttles import BurstUserRateThrottle

RATES = {"user_burst": "3/min", "staff_burst": "6/min"}


class _View(APIView):
    throttle_classes = [BurstUserRateThrottle]

    def get(self, request):
        return Response({"ok": True})


@mock.patch.object(BurstUserRateThrottle, "THROTTLE_RATES", RATES)
class ThrottleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="thr_user", email="thr_user@example.com", password="x")
        cls.staff = User.objects.create_user(username="thr_staff", email="thr_staff@example.com", password="x", is_staff=True)

    def setUp(self):
        cache.clear()

    def hits(self, user, n):
        factory, view, out = APIRequestFactory(), _View.as_view(), []
        for _ in range(n):
            req = factory.get("/throttled/")
            force_authenticate(req, user=user)
            out.append(view(req))
        return out

    def test_user_gets_429_with_retry_after(self):
        responses = self.hits(self.user, 4)
        self.assertEqual([r.status_code for r in responses[:3]], [200, 200, 200])
        self.assertEqual(responses[3].status_code, 429)
        self.assertGreater(int(responses[3]["Retry-After"]), 0)

    def test_platform_admin_uses_the_higher_scope(self):
        codes = [r.status_code for r in self.hits(self.staff, 7)]
        self.assertEqual(codes[:6], [200] * 6)
        self.assertEqual(codes[6], 429)

    def test_counters_are_separate(self):
        self.hits(self.user, 3)
        self.assertEqual(self.hits(self.staff, 1)[0].status_code, 200)
