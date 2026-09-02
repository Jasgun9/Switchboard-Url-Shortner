from django.urls import path

from web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("register", views.register, name="register"),
    path("login", views.login_view, name="login"),
    path("logout", views.logout_view, name="logout"),
    path("docs", views.api_docs, name="api-docs"),
    path("dashboard", views.dashboard, name="dashboard"),
    path("links/new", views.link_create, name="link-create"),
    path("links/<int:pk>", views.link_detail, name="link-detail"),
    path("links/<int:pk>/edit", views.link_edit, name="link-edit"),
    path("links/<int:pk>/delete", views.link_delete, name="link-delete"),
    path("links/<int:pk>/analytics", views.link_analytics, name="link-analytics"),
    path("keys", views.api_keys, name="api-keys"),
    path("keys/<int:pk>/revoke", views.api_key_revoke, name="api-key-revoke"),
]
