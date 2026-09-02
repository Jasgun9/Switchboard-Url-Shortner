from io import BytesIO

import qrcode
from django.conf import settings
from django.core.cache import cache


def png_for(short_url):
    # PNG bytes for a short link, cached because the image never changes.
    key = f"qr:{short_url.code}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    image = qrcode.make(short_url.short_url, box_size=8, border=2)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()

    cache.set(key, data, settings.QR_CACHE_TTL)
    return data
