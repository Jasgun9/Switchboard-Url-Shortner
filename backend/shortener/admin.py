from django.contrib import admin

from .models import APIKey, ClickEvent, ShortURL


@admin.register(ShortURL)
class ShortURLAdmin(admin.ModelAdmin):
    list_display = ["code", "destination", "owner", "click_count", "expires_at", "is_active", "deleted_at"]
    list_filter = ["is_active", "is_custom_alias"]
    search_fields = ["code", "destination", "owner__email"]
    readonly_fields = ["click_count", "last_clicked_at", "created_at", "updated_at", "password_hash"]
    list_select_related = ["owner"]
    raw_id_fields = ["owner"]


@admin.register(ClickEvent)
class ClickEventAdmin(admin.ModelAdmin):
    list_display = ["short_url", "created_at", "country", "device", "browser", "os", "referrer_host"]
    list_filter = ["device", "country"]
    list_select_related = ["short_url"]
    raw_id_fields = ["short_url"]
    date_hierarchy = "created_at"


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "prefix", "owner", "created_at", "last_used_at", "expires_at", "revoked_at"]
    search_fields = ["name", "prefix", "owner__email"]
    readonly_fields = ["prefix", "secret_hash", "created_at", "last_used_at"]
    list_select_related = ["owner"]
    raw_id_fields = ["owner"]
