import secrets

from django.conf import settings

from shortener.validators import RESERVED_ALIASES

# No 0/O and no 1/l/I. People read these out loud and retype them by hand.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def generate_code(length=None):
    length = length or settings.SHORT_CODE_LENGTH
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if code.lower() not in RESERVED_ALIASES:
            return code
