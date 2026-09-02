from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api import views

router = DefaultRouter()
router.register("urls", views.ShortURLViewSet, basename="shorturl")
router.register("api-keys", views.APIKeyViewSet, basename="apikey")

urlpatterns = [
    path("auth/csrf/", views.csrf, name="auth-csrf"),
    path("auth/register/", views.register, name="auth-register"),
    path("auth/login/", views.login_view, name="auth-login"),
    path("auth/logout/", views.logout_view, name="auth-logout"),
    path("auth/me/", views.me, name="auth-me"),
    path("qr/<str:code>.png", views.qr_code, name="qr-code"),
    path("", include(router.urls)),
]
