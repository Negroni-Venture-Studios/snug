from django.urls import path

from snug.accounts import admin_views, views

app_name = "accounts"

urlpatterns = [
    path("login", views.login_view, name="login"),
    path("login/code", views.login_code_view, name="login_code"),
    path("login/recovery", views.login_recovery_view, name="login_recovery"),
    path("login/forgot", views.forgot_view, name="forgot"),
    path("logout", views.logout_view, name="logout"),
    path("settings/", admin_views.settings_view, name="settings"),
    path("settings/password", admin_views.change_password_view, name="change_password"),
    path(
        "settings/authenticator",
        admin_views.replace_authenticator_view,
        name="replace_authenticator",
    ),
    path("settings/notifications", admin_views.notifications_view, name="notifications"),
    path("settings/email", admin_views.email_view, name="email"),
    path("settings/email/test", admin_views.email_test_view, name="email_test"),
    path("settings/email/google/connect", admin_views.google_connect_view, name="google_connect"),
    path(
        "settings/email/google/callback", admin_views.google_callback_view, name="google_callback"
    ),
    path("settings/email/google/confirm", admin_views.google_confirm_view, name="google_confirm"),
    path(
        "settings/email/google/disconnect",
        admin_views.google_disconnect_view,
        name="google_disconnect",
    ),
    path("settings/email/clear", admin_views.email_clear_view, name="email_clear"),
    path("settings/branding", admin_views.branding_view, name="branding"),
    path("settings/logo", admin_views.logo_view, name="logo"),
    path("settings/icon", admin_views.icon_view, name="icon"),
    path("settings/privacy", admin_views.privacy_view, name="privacy"),
    path("settings/office", admin_views.office_view, name="office"),
    path("settings/nda", admin_views.nda_view, name="nda"),
    path("settings/retention", admin_views.retention_view, name="retention"),
    path("settings/admins/invite", admin_views.invite_view, name="invite"),
    path("settings/admins/remove", admin_views.remove_view, name="remove"),
    path("settings/admins/reset", admin_views.reset_view, name="reset"),
    path("setup/<str:token>", admin_views.setup_view, name="setup"),
]
