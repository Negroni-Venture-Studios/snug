from django.urls import path

from snug.accounts import admin_views, views

app_name = "accounts"

urlpatterns = [
    path("login", views.login_view, name="login"),
    path("login/code", views.login_code_view, name="login_code"),
    path("login/recovery", views.login_recovery_view, name="login_recovery"),
    path("logout", views.logout_view, name="logout"),
    path("settings/", admin_views.settings_view, name="settings"),
    path("settings/privacy", admin_views.privacy_view, name="privacy"),
    path("settings/nda", admin_views.nda_view, name="nda"),
    path("settings/admins/invite", admin_views.invite_view, name="invite"),
    path("settings/admins/remove", admin_views.remove_view, name="remove"),
    path("settings/admins/reset", admin_views.reset_view, name="reset"),
    path("setup/<str:token>", admin_views.setup_view, name="setup"),
]
