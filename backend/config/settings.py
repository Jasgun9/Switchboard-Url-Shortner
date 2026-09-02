from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    LOG_LEVEL=(str, "INFO"),
    CLICK_RETENTION_DAYS=(int, 180),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-development-key-do-not-use-in-production")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS") or (["*"] if DEBUG else [])
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# Both services run these settings and differ only in the URLconf they mount.
# That's what keeps the dashboard and the API off the redirect host.
ROOT_URLCONF = env("DJANGO_ROOT_URLCONF", default="config.urls_web")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "rest_framework",
    "accounts",
    "shortener",
    "api",
    "web",
    "redirector",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi_web.application"

# Postgres in production, SQLite for local work. Nothing in the app writes
# backend-specific SQL, so swapping DATABASE_URL is the whole switch.
DATABASES = {
    "default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"].setdefault("CONN_MAX_AGE", env.int("DB_CONN_MAX_AGE", default=60))
# Reused connections outlive a database restart and come back dead. This pings
# them first, which costs a round trip and saves a 500 on every deploy of the
# database itself.
DATABASES["default"].setdefault("CONN_HEALTH_CHECKS", True)

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/dashboard"
LOGOUT_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Redis -------------------------------------------------------------------

REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # Redis down should mean slow, not broken. Short connect timeout because
            # Redis is on the same box, so anything slower is already dead -- and a
            # generous value turns every cache read into a multi-second stall.
            "IGNORE_EXCEPTIONS": True,
            "SOCKET_CONNECT_TIMEOUT": 0.3,
            "SOCKET_TIMEOUT": 1,
            "CONNECTION_POOL_KWARGS": {"retry_on_timeout": False},
        },
        "KEY_PREFIX": "urlshort",
    }
}
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_NAME = "sessionid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    X_FRAME_OPTIONS = "DENY"
    # W008 wants SECURE_SSL_REDIRECT, but nginx redirects :80 to :443 long before
    # Django sees anything. Turning it on here adds a branch nothing reaches.
    SILENCED_SYSTEM_CHECKS = ["security.W008"]

# Behind Cloudflare and nginx REMOTE_ADDR is always the proxy. Trust only the
# header named here, never whatever the client sends.
CLIENT_IP_HEADER = env("CLIENT_IP_HEADER", default="HTTP_CF_CONNECTING_IP")

DATA_UPLOAD_MAX_MEMORY_SIZE = 512 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

# --- Celery ------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 4
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = "UTC"

# Housekeeping only. Anything a request triggers gets dispatched from the request.
CELERY_BEAT_SCHEDULE = {
    "purge-old-clicks": {
        "task": "shortener.tasks.purge_old_clicks",
        "schedule": crontab(hour="3", minute="20"),
    },
    "purge-deleted-urls": {
        "task": "shortener.tasks.purge_deleted_urls",
        "schedule": crontab(hour="3", minute="40"),
    },
}

# --- REST framework ----------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.auth.APIKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "api.errors.exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

# --- Application settings ----------------------------------------------------

SHORT_DOMAIN = env("SHORT_DOMAIN", default="http://localhost:8001")
WEB_DOMAIN = env("WEB_DOMAIN", default="http://localhost:8000")

# Shortening a link to 127.0.0.1 only makes sense while developing.
BLOCK_PRIVATE_DESTINATIONS = env.bool("BLOCK_PRIVATE_DESTINATIONS", default=not DEBUG)

# Codes start as short as possible and grow a character at a time once a length
# fills up. Two characters is 3,249 codes, which is also small enough that
# anyone can walk the whole space in a few minutes -- raise the minimum if links
# being discoverable matters more than them being tiny.
SHORT_CODE_MIN_LENGTH = env.int("SHORT_CODE_MIN_LENGTH", default=2)
SHORT_CODE_MAX_LENGTH = env.int("SHORT_CODE_MAX_LENGTH", default=8)
# Failed inserts are the signal that a length is crowded. Few enough that
# stepping up is cheap, enough that a length is genuinely used up first.
SHORT_CODE_ATTEMPTS_PER_LENGTH = 5
ALIAS_MIN_LENGTH = 3
ALIAS_MAX_LENGTH = 32

RESOLVE_CACHE_TTL = env.int("RESOLVE_CACHE_TTL", default=300)
MISSING_CACHE_TTL = 60
QR_CACHE_TTL = 86400

# Salted so click rows never hold a raw IP.
IP_HASH_SALT = env("IP_HASH_SALT", default=SECRET_KEY)
CLICK_RETENTION_DAYS = env("CLICK_RETENTION_DAYS")

GEOIP_PATH = env("GEOIP_PATH", default="")

# All limits in one place so they can be tuned without touching views.
# (max_requests, window_seconds)
RATE_LIMITS = {
    "anon_create": env.tuple("RATE_LIMIT_ANON_CREATE", cast=int, default=(10, 3600)),
    "user_create": env.tuple("RATE_LIMIT_USER_CREATE", cast=int, default=(120, 3600)),
    "login": env.tuple("RATE_LIMIT_LOGIN", cast=int, default=(10, 900)),
    "register": env.tuple("RATE_LIMIT_REGISTER", cast=int, default=(5, 3600)),
    "link_password": env.tuple("RATE_LIMIT_LINK_PASSWORD", cast=int, default=(8, 900)),
    "api": env.tuple("RATE_LIMIT_API", cast=int, default=(1000, 3600)),
    "redirect": env.tuple("RATE_LIMIT_REDIRECT", cast=int, default=(600, 60)),
}

LINK_PASSWORD_COOKIE_MAX_AGE = 3600

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "accounts": {"level": env("LOG_LEVEL"), "propagate": True},
        "api": {"level": env("LOG_LEVEL"), "propagate": True},
        "shortener": {"level": env("LOG_LEVEL"), "propagate": True},
        "redirector": {"level": env("LOG_LEVEL"), "propagate": True},
        "web": {"level": env("LOG_LEVEL"), "propagate": True},
        "django.request": {"level": "ERROR", "propagate": True},
    },
}
