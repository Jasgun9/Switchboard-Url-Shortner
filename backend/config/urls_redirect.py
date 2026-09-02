from django.urls import path

from core import health
from redirector import views

urlpatterns = [
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("robots.txt", views.robots, name="robots"),
    path("", views.index, name="index"),
    path("<str:code>", views.resolve, name="resolve"),
]

handler404 = "redirector.views.not_found"
handler500 = "redirector.views.server_error"
