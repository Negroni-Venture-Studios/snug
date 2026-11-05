from django.urls import path

from snug.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login", views.login_view, name="login"),
    path("login/code", views.login_code_view, name="login_code"),
    path("login/recovery", views.login_recovery_view, name="login_recovery"),
    path("logout", views.logout_view, name="logout"),
]
