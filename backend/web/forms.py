from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone

from shortener.models import ShortURL
from shortener.validators import validate_alias, validate_destination

User = get_user_model()

MIN_LINK_PASSWORD_LENGTH = 6

# UTC in and out. Working out the visitor's zone means a cookie handshake or
# a JS conversion layer, for the sake of two fields.
DATETIME_LOCAL = {"type": "datetime-local"}
DATETIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M"


def datetime_local_field(**kwargs):
    return forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs=DATETIME_LOCAL, format=DATETIME_LOCAL_FORMAT),
        **kwargs,
    )


class RegisterForm(forms.Form):
    email = forms.EmailField(max_length=254)
    display_name = forms.CharField(max_length=60, required=False)
    password = forms.CharField(max_length=128, widget=forms.PasswordInput, min_length=10)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password

    def save(self):
        return User.objects.create_user(
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password"],
            display_name=self.cleaned_data["display_name"],
        )


class LoginForm(forms.Form):
    email = forms.EmailField(max_length=254)
    password = forms.CharField(max_length=128, widget=forms.PasswordInput)


class ShortenForm(forms.Form):
    """The home page form. Everything past `destination` sits behind the
    advanced-options disclosure."""

    destination = forms.CharField(
        max_length=2048,
        widget=forms.URLInput(
            attrs={"placeholder": "https://example.com/some/very/long/url", "autocomplete": "off", "spellcheck": "false"}
        ),
    )
    alias = forms.CharField(max_length=64, required=False, strip=False, label="Custom alias")
    expires_at = datetime_local_field(label="Expires at")
    password = forms.CharField(
        max_length=128, required=False, widget=forms.PasswordInput(render_value=False), label="Password"
    )

    def clean_destination(self):
        return validate_destination(self.cleaned_data["destination"])

    def clean_alias(self):
        alias = self.cleaned_data["alias"]
        return validate_alias(alias) if alias else ""

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at is not None and expires_at <= timezone.now():
            raise ValidationError("Expiry must be in the future.")
        return expires_at

    def clean_password(self):
        password = self.cleaned_data["password"]
        if password and len(password) < MIN_LINK_PASSWORD_LENGTH:
            raise ValidationError(f"Link password must be at least {MIN_LINK_PASSWORD_LENGTH} characters.")
        return password


class LinkForm(forms.Form):
    # Create and edit share this form; `instance` switches off the alias field.

    destination = forms.CharField(max_length=2048, widget=forms.URLInput)
    title = forms.CharField(max_length=120, required=False)
    alias = forms.CharField(max_length=64, required=False, strip=False)
    expires_at = datetime_local_field()
    password = forms.CharField(max_length=128, required=False, widget=forms.PasswordInput)
    remove_password = forms.BooleanField(required=False)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is None:
            del self.fields["remove_password"]
            del self.fields["is_active"]
        else:
            # The code is in every link already shared, so it can't move.
            del self.fields["alias"]

    def clean_destination(self):
        return validate_destination(self.cleaned_data["destination"])

    def clean_alias(self):
        alias = self.cleaned_data["alias"]
        return validate_alias(alias) if alias else ""

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at is not None and expires_at <= timezone.now():
            raise ValidationError("Expiry must be in the future.")
        return expires_at

    def clean_password(self):
        password = self.cleaned_data["password"]
        if password and len(password) < MIN_LINK_PASSWORD_LENGTH:
            raise ValidationError(f"Link password must be at least {MIN_LINK_PASSWORD_LENGTH} characters.")
        return password

    @staticmethod
    def initial_from(link: ShortURL):
        return {
            "destination": link.destination,
            "title": link.title,
            "expires_at": timezone.localtime(link.expires_at) if link.expires_at else None,
            "is_active": link.is_active,
        }

    def apply_to(self, link: ShortURL):
        link.destination = self.cleaned_data["destination"]
        link.title = self.cleaned_data["title"]
        link.expires_at = self.cleaned_data["expires_at"]
        link.is_active = self.cleaned_data["is_active"]

        if self.cleaned_data["remove_password"]:
            link.set_link_password("")
        elif self.cleaned_data["password"]:
            link.set_link_password(self.cleaned_data["password"])

        link.save()
        return link


class APIKeyForm(forms.Form):
    name = forms.CharField(max_length=60)
    expires_at = datetime_local_field()

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at is not None and expires_at <= timezone.now():
            raise ValidationError("Expiry must be in the future.")
        return expires_at
