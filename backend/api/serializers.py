from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from api.errors import APIError
from shortener.models import AliasTaken, APIKey, CodeGenerationError, ShortURL, create_short_url
from shortener.validators import validate_alias, validate_destination

User = get_user_model()

MAX_LINK_PASSWORD_LENGTH = 128


class ShortURLSerializer(serializers.ModelSerializer):
    short_url = serializers.CharField(read_only=True)
    has_password = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    qr_url = serializers.SerializerMethodField()

    alias = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=MAX_LINK_PASSWORD_LENGTH,
    )

    class Meta:
        model = ShortURL
        fields = [
            "id",
            "code",
            "short_url",
            "destination",
            "title",
            "alias",
            "password",
            "has_password",
            "is_active",
            "is_custom_alias",
            "is_expired",
            "expires_at",
            "click_count",
            "last_clicked_at",
            "qr_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "code",
            "is_custom_alias",
            "click_count",
            "last_clicked_at",
            "created_at",
            "updated_at",
        ]

    def get_qr_url(self, obj):
        path = reverse("qr-code", kwargs={"code": obj.code})
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path

    def validate_destination(self, value):
        return validate_destination(value)

    def validate_alias(self, value):
        if not value:
            return ""
        return validate_alias(value)

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value

    def validate_password(self, value):
        if value and len(value) < 6:
            raise serializers.ValidationError("Link password must be at least 6 characters.")
        return value

    def validate(self, attrs):
        if self.instance is not None and "alias" in attrs:
            raise serializers.ValidationError(
                {"alias": "A short code cannot be changed after creation; create a new link instead."}
            )
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        try:
            return create_short_url(
                destination=validated_data["destination"],
                owner=request.user if request.user.is_authenticated else None,
                title=validated_data.get("title", ""),
                alias=validated_data.get("alias", ""),
                expires_at=validated_data.get("expires_at"),
                password=validated_data.get("password") or "",
            )
        except AliasTaken:
            raise APIError("ALIAS_ALREADY_EXISTS", "The requested alias is already in use.", 409)
        except CodeGenerationError:
            raise APIError(
                "CODE_GENERATION_FAILED",
                "Could not allocate a short code. Please try again.",
                503,
            )

    def update(self, instance, validated_data):
        validated_data.pop("alias", None)

        if "password" in validated_data:
            instance.set_link_password(validated_data.pop("password") or "")

        for field in ["destination", "title", "expires_at", "is_active"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        instance.save()
        return instance


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "display_name", "date_joined"]
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, max_length=128)
    display_name = serializers.CharField(max_length=60, required=False, allow_blank=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value):
        try:
            password_validation.validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            display_name=validated_data.get("display_name", ""),
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, max_length=128)


class APIKeySerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = APIKey
        fields = ["id", "name", "prefix", "is_active", "created_at", "last_used_at", "expires_at", "revoked_at"]
        read_only_fields = ["id", "prefix", "created_at", "last_used_at", "revoked_at"]

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value
