from django.contrib.auth import get_user_model
from django.test import override_settings

from tests.base import AppTestCase

User = get_user_model()


class RegistrationTests(AppTestCase):
    def test_registers_and_signs_the_user_in(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": "New@Example.com", "password": "correct-horse-battery"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["email"], "new@example.com")
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 200)

    def test_password_is_hashed(self):
        self.client.post(
            "/api/v1/auth/register/",
            {"email": "hash@example.com", "password": "correct-horse-battery"},
            content_type="application/json",
        )

        user = User.objects.get(email="hash@example.com")
        self.assertNotEqual(user.password, "correct-horse-battery")
        self.assertTrue(user.check_password("correct-horse-battery"))

    def test_weak_passwords_are_rejected(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": "weak@example.com", "password": "password"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())

    def test_duplicate_email_is_rejected(self):
        self.make_user("taken@example.com")

        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": "taken@example.com", "password": "correct-horse-battery"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)


class LoginTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user("login@example.com", "correct-horse-battery")

    def test_valid_credentials_start_a_session(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "correct-horse-battery"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me/").json()["email"], "login@example.com")

    def test_invalid_credentials_are_rejected_without_leaking_which_part_was_wrong(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "wrong-password"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "INVALID_CREDENTIALS")
        self.assertEqual(response.json()["error"]["message"], "Incorrect email or password.")

    def test_logout_ends_the_session(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.post("/api/v1/auth/logout/").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 401)

    @override_settings(RATE_LIMITS={"login": (3, 900), "api": (100, 60)})
    def test_login_attempts_are_rate_limited(self):
        for _ in range(3):
            self.client.post(
                "/api/v1/auth/login/",
                {"email": "login@example.com", "password": "wrong"},
                content_type="application/json",
            )

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "correct-horse-battery"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 429)

    @override_settings(RATE_LIMITS={"login": (3, 900), "api": (100, 60)})
    def test_successful_login_clears_the_counter(self):
        self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "wrong"},
            content_type="application/json",
        )
        self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "correct-horse-battery"},
            content_type="application/json",
        )

        for _ in range(3):
            response = self.client.post(
                "/api/v1/auth/login/",
                {"email": "login@example.com", "password": "correct-horse-battery"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
