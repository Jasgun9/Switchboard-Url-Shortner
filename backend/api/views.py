import logging

from django.contrib.auth import authenticate, login, logout
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from api.errors import APIError
from api.serializers import (
    APIKeySerializer,
    LoginSerializer,
    RegisterSerializer,
    ShortURLSerializer,
    UserSerializer,
)
from api.throttling import APIThrottle, URLCreateThrottle
from core import ratelimit
from core.clientinfo import client_ip
from shortener import qr
from shortener.analytics import summary as analytics_summary
from shortener.models import APIKey, ShortURL, create_api_key

log = logging.getLogger(__name__)

ORDERING_FIELDS = {
    "created_at": "created_at",
    "-created_at": "-created_at",
    "click_count": "click_count",
    "-click_count": "-click_count",
}


class ShortURLViewSet(viewsets.ModelViewSet):
    serializer_class = ShortURLSerializer
    throttle_classes = [APIThrottle]

    def get_queryset(self):
        # Scoping every query to request.user is what prevents one account from
        # reaching another's links by guessing an id.
        queryset = ShortURL.objects.filter(owner=self.request.user, deleted_at__isnull=True)

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(destination__icontains=search) | Q(code__icontains=search) | Q(title__icontains=search)
            )

        state = self.request.query_params.get("status", "")
        now = timezone.now()
        if state == "active":
            queryset = queryset.filter(is_active=True).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            )
        elif state == "expired":
            queryset = queryset.filter(expires_at__lte=now)
        elif state == "disabled":
            queryset = queryset.filter(is_active=False)

        ordering = ORDERING_FIELDS.get(self.request.query_params.get("ordering", ""), "-created_at")
        return queryset.order_by(ordering, "-id")

    def get_permissions(self):
        # Anonymous visitors may shorten a link from the home page; everything
        # else needs an account.
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_throttles(self):
        if self.action == "create":
            return [URLCreateThrottle()]
        return super().get_throttles()

    def perform_destroy(self, instance):
        # Soft delete: the row and its click history survive, but the link
        # stops resolving and its code is released for reuse.
        instance.soft_delete()

    @action(detail=True, methods=["get"])
    def analytics(self, request, pk=None):
        short_url = self.get_object()
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            raise APIError("INVALID_PARAMETER", "days must be an integer.")
        return Response(analytics_summary(short_url, days))


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def qr_code(request, code):
    """Public: the QR only encodes the short link, which is already public."""
    verdict = ratelimit.consume("redirect", f"qr:{client_ip(request)}")
    if not verdict.allowed:
        raise APIError("RATE_LIMITED", "Too many requests.", status.HTTP_429_TOO_MANY_REQUESTS)

    short_url = ShortURL.objects.alive().filter(code=code).first()
    if short_url is None:
        raise APIError("NOT_FOUND", "No such short link.", status.HTTP_404_NOT_FOUND)

    response = HttpResponse(qr.png_for(short_url), content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    response["Content-Disposition"] = f'inline; filename="{code}.png"'
    return response


class APIKeyViewSet(viewsets.ModelViewSet):
    """Key management is session-only on purpose: a leaked key cannot mint more keys."""

    serializer_class = APIKeySerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [APIThrottle]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return APIKey.objects.filter(owner=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key, token = create_api_key(
            request.user,
            serializer.validated_data["name"],
            serializer.validated_data.get("expires_at"),
        )
        log.info("api key %s created for user %s", key.prefix, request.user.pk)

        body = self.get_serializer(key).data
        # The only time the secret is ever returned.
        body["token"] = token
        return Response(body, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        instance.revoke()


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf(request):
    return Response({"detail": "CSRF cookie set."})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
def register(request):
    if not ratelimit.consume("register", f"ip:{client_ip(request)}").allowed:
        raise APIError("RATE_LIMITED", "Too many registration attempts.", status.HTTP_429_TOO_MANY_REQUESTS)

    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    login(request, user)
    log.info("registered user %s", user.pk)
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
def login_view(request):
    identifier = f"ip:{client_ip(request)}"
    if not ratelimit.consume("login", identifier).allowed:
        raise APIError("RATE_LIMITED", "Too many login attempts.", status.HTTP_429_TOO_MANY_REQUESTS)

    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = authenticate(
        request,
        username=serializer.validated_data["email"].strip().lower(),
        password=serializer.validated_data["password"],
    )
    if user is None:
        log.info("failed login attempt from %s", identifier)
        raise APIError("INVALID_CREDENTIALS", "Incorrect email or password.", status.HTTP_401_UNAUTHORIZED)

    login(request, user)
    ratelimit.reset("login", identifier)
    return Response(UserSerializer(user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(UserSerializer(request.user).data)
