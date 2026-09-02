from django.contrib import admin
from django.urls import include, path

from core import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("api.urls")),
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("", include("web.urls")),
]

handler404 = "web.views.not_found"
handler500 = "web.views.server_error"
