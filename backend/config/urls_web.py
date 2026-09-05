from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core import health
from web import views as web_views
from web.sitemaps import PublicPages

sitemaps = {"pages": PublicPages}

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=static('favicon.ico'), permanent=True)),
    path("admin/", admin.site.urls),
    path("api/v1/", include("api.urls")),
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("robots.txt", web_views.robots, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="django.contrib.sitemaps.views.sitemap"),
    path("", include("web.urls")),
]

handler404 = "web.views.not_found"
handler500 = "web.views.server_error"
