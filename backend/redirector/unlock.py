"""Signed cookies that remember a visitor already entered a link's password.

The cookie is scoped to the link's own path and carries the link's
`password_version`, so changing the password invalidates every cookie handed out
under the old one.
"""

from django.conf import settings
from django.core import signing

SALT = "link-password"


def cookie_name(code):
    return f"lp_{code}"


def issue(response, code, payload):
    token = signing.dumps([payload["id"], payload["password_version"]], salt=SALT)
    response.set_cookie(
        cookie_name(code),
        token,
        max_age=settings.LINK_PASSWORD_COOKIE_MAX_AGE,
        path=f"/{code}",
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
    )


def is_unlocked(request, code, payload):
    token = request.COOKIES.get(cookie_name(code))
    if not token:
        return False
    try:
        url_id, version = signing.loads(
            token, salt=SALT, max_age=settings.LINK_PASSWORD_COOKIE_MAX_AGE
        )
    except (signing.BadSignature, ValueError):
        return False
    return url_id == payload["id"] and version == payload["password_version"]
