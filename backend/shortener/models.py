import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from shortener import cache
from shortener.shortcodes import generate_code

API_KEY_PREFIX_LENGTH = 8
API_KEY_SECRET_BYTES = 32


class CodeGenerationError(Exception):
    """Raised when repeated random codes all collided with existing rows."""


class AliasTaken(Exception):
    """Raised when a requested custom alias is already claimed."""


class ShortURLQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True, is_active=True, code_released_at__isnull=True)

    def owned_by(self, user):
        return self.filter(owner=user)


class ShortURL(models.Model):
    code = models.CharField(max_length=settings.ALIAS_MAX_LENGTH)
    destination = models.TextField()
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="urls",
    )
    title = models.CharField(max_length=120, blank=True)
    is_custom_alias = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    password_hash = models.CharField(max_length=128, blank=True)
    password_updated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    # Set once this row stops owning its code, which is what lets a deleted or
    # expired link's alias be claimed again while its click history survives.
    code_released_at = models.DateTimeField(null=True, blank=True)
    click_count = models.BigIntegerField(default=0)
    last_clicked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ShortURLQuerySet.as_manager()

    class Meta:
        constraints = [
            # Only one row may own a code at a time. Released rows drop out of
            # the index, so their alias becomes claimable again — and this is
            # still the database deciding who wins a contested claim.
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(code_released_at__isnull=True),
                name="shorturl_unique_live_code",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="shorturl_owner_created_idx"),
            models.Index(
                fields=["expires_at"],
                condition=models.Q(expires_at__isnull=False),
                name="shorturl_expires_idx",
            ),
        ]

    def __str__(self):
        return f"/{self.code}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.forget(self.code)

    def delete(self, *args, **kwargs):
        code = self.code
        result = super().delete(*args, **kwargs)
        cache.forget(code)
        return result

    @property
    def short_url(self):
        return f"{settings.SHORT_DOMAIN.rstrip('/')}/{self.code}"

    @property
    def has_password(self):
        return bool(self.password_hash)

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def is_code_released(self):
        """True once someone else is free to claim this link's code."""
        return self.code_released_at is not None

    def is_resolvable(self):
        return self.is_active and not self.is_deleted and not self.is_expired and not self.is_code_released

    def set_link_password(self, raw_password):
        self.password_hash = make_password(raw_password) if raw_password else ""
        self.password_updated_at = timezone.now()

    def check_link_password(self, raw_password):
        if not self.password_hash:
            return True
        return check_password(raw_password, self.password_hash)

    def soft_delete(self):
        """Retire the link and free its code straight away.

        The row and its clicks stay, so the history survives; only the claim on
        the code is given up.
        """
        now = timezone.now()
        self.deleted_at = now
        self.code_released_at = now
        self.is_active = False
        self.save(update_fields=["deleted_at", "code_released_at", "is_active", "updated_at"])


def save_with_random_code(url):
    """Insert an unsaved ShortURL, retrying until a generated code is free.

    The unique constraint is what actually decides the winner: two requests can
    generate the same code and both pass any prior existence check.
    """
    for _ in range(settings.SHORT_CODE_MAX_ATTEMPTS):
        url.code = generate_code()
        try:
            # Own savepoint per attempt, otherwise a failed INSERT poisons the
            # surrounding transaction on PostgreSQL.
            with transaction.atomic():
                url.save(force_insert=True)
        except IntegrityError:
            url.pk = None
            continue
        return url
    raise CodeGenerationError("Could not allocate a unique short code.")


def release_reclaimable_code(code):
    """Give up the claim on `code` if its current holder is retired.

    A link that is deleted or already past its expiry no longer resolves, so
    holding onto the alias serves nobody. Disabled links are left alone: being
    switched off is meant to be reversible.
    """
    now = timezone.now()
    holders = ShortURL.objects.filter(code=code, code_released_at__isnull=True).filter(
        models.Q(deleted_at__isnull=False) | models.Q(expires_at__lte=now)
    )
    # A queryset update skips save(), so the resolve cache is cleared by hand.
    released = holders.update(code_released_at=now)
    if released:
        cache.forget(code)
    return released


def create_short_url(*, destination, owner=None, title="", alias="", expires_at=None, password=""):
    """Create a link. Both the HTML forms and the API go through here.

    Assumes its arguments are already validated; it only owns the part that
    cannot be validated up front, which is who wins a contested code.
    """
    url = ShortURL(destination=destination, owner=owner, title=title, expires_at=expires_at)
    if password:
        url.set_link_password(password)

    if not alias:
        return save_with_random_code(url)

    url.code = alias
    url.is_custom_alias = True
    try:
        # Releasing and inserting share a transaction: if someone else wins the
        # insert, the release rolls back with it and their claim stands.
        with transaction.atomic():
            release_reclaimable_code(alias)
            url.save(force_insert=True)
    except IntegrityError:
        # Two requests can both see the alias as free and both try to insert it.
        raise AliasTaken(alias)
    return url


class ClickEvent(models.Model):
    class Device(models.TextChoices):
        DESKTOP = "desktop"
        MOBILE = "mobile"
        TABLET = "tablet"
        BOT = "bot"
        UNKNOWN = "unknown"

    short_url = models.ForeignKey(ShortURL, on_delete=models.CASCADE, related_name="clicks")
    created_at = models.DateTimeField(default=timezone.now)
    ip_hash = models.CharField(max_length=32, blank=True)
    country = models.CharField(max_length=2, blank=True)
    region = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=64, blank=True)
    device = models.CharField(max_length=16, choices=Device, default=Device.UNKNOWN)
    browser = models.CharField(max_length=40, blank=True)
    os = models.CharField(max_length=40, blank=True)
    referrer_host = models.CharField(max_length=255, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["short_url", "-created_at"], name="click_url_created_idx"),
            models.Index(fields=["created_at"], name="click_created_idx"),
        ]

    def __str__(self):
        return f"click on /{self.short_url_id} at {self.created_at:%Y-%m-%d %H:%M}"


class APIKeyQuerySet(models.QuerySet):
    def usable(self):
        now = timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class APIKey(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=60)
    prefix = models.CharField(max_length=API_KEY_PREFIX_LENGTH, unique=True)
    # SHA-256 rather than a password hasher: the secret is 256 bits of CSPRNG
    # output, so there is nothing to brute force and lookups stay cheap.
    secret_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = APIKeyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix})"

    @property
    def is_active(self):
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])


def hash_api_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def create_api_key(owner, name, expires_at=None):
    """Create a key and return (instance, raw_token). The raw token is the only
    time the secret exists outside the caller's response."""
    for _ in range(5):
        prefix = secrets.token_hex(API_KEY_PREFIX_LENGTH // 2)
        secret = secrets.token_urlsafe(API_KEY_SECRET_BYTES)
        try:
            with transaction.atomic():
                key = APIKey.objects.create(
                    owner=owner,
                    name=name,
                    prefix=prefix,
                    secret_hash=hash_api_secret(secret),
                    expires_at=expires_at,
                )
        except IntegrityError:
            continue
        return key, f"usk_{prefix}_{secret}"
    raise CodeGenerationError("Could not allocate a unique API key prefix.")
