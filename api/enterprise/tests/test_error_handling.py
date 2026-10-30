from django.test import SimpleTestCase

from api.enterprise.error_handling import company_access_denied, generic_validation_error


class EnterpriseErrorHandlingTests(SimpleTestCase):
    def test_company_access_denied_hides_internal_message(self):
        exc = company_access_denied(
            PermissionError("User 'rafa' has no active membership in company 77."),
            user_id=11,
            company_id=77,
        )

        self.assertEqual(str(exc.detail), "You do not have access to this company.")

    def test_generic_validation_error_hides_internal_message(self):
        exc = generic_validation_error(
            ValueError("database timeout on internal job 55"),
            detail="The request could not be completed.",
        )

        self.assertEqual(exc.detail["detail"], "The request could not be completed.")
