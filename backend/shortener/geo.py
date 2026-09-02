"""GeoIP lookups against a local MaxMind database.

A local .mmdb keeps the redirect path free of third-party network calls. Only
`lookup()` knows where the data comes from, so swapping in another provider is a
change to this one function.
"""

import logging
import threading

import geoip2.database
import geoip2.errors
from django.conf import settings

log = logging.getLogger(__name__)

EMPTY = {"country": "", "region": "", "city": ""}

_lock = threading.Lock()
_reader = None
_unavailable = False


def _get_reader():
    global _reader, _unavailable

    if _reader is not None or _unavailable:
        return _reader

    with _lock:
        if _reader is None and not _unavailable:
            path = settings.GEOIP_PATH
            if not path:
                _unavailable = True
                return None
            try:
                _reader = geoip2.database.Reader(path)
            except (OSError, geoip2.errors.GeoIP2Error) as exc:
                log.warning("GeoIP database unavailable at %s: %s", path, exc)
                _unavailable = True
    return _reader


def lookup(ip):
    reader = _get_reader()
    if reader is None or not ip:
        return dict(EMPTY)

    try:
        response = reader.city(ip)
    except geoip2.errors.AddressNotFoundError:
        return dict(EMPTY)
    except (ValueError, geoip2.errors.GeoIP2Error) as exc:
        log.warning("GeoIP lookup failed: %s", exc)
        return dict(EMPTY)

    region = response.subdivisions.most_specific.name if response.subdivisions else ""
    return {
        "country": (response.country.iso_code or "")[:2],
        "region": (region or "")[:64],
        "city": (response.city.name or "")[:64],
    }


def reset():
    """Drop the cached reader; used by tests and after a database swap."""
    global _reader, _unavailable
    with _lock:
        if _reader is not None:
            _reader.close()
        _reader = None
        _unavailable = False
