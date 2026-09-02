from django.apps import AppConfig


class ShortenerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shortener"

    def ready(self):
        from shortener import checks  # noqa: F401  (registers the database checks)
