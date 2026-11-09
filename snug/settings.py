"""Django settings for Snug, built from the checked configuration.

Nothing here is meant to be edited per instance: operators set the
environment variables documented in docs/configuration.md, and
snug.config.load_config() refuses to start if any of them is wrong.
"""

from pathlib import Path

from django.utils.csp import CSP

from snug.config import load_config

CONFIG = load_config()
BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = CONFIG.secret_key
DEBUG = CONFIG.debug
ALLOWED_HOSTS = [CONFIG.domain]
CSRF_TRUSTED_ORIGINS = [CONFIG.origin]

# The Django admin is deliberately absent: it would not enforce TOTP, and it
# is attack surface Snug does not need. ruff bans importing it.
INSTALLED_APPS = [
    # First, so its `changepassword` replaces Django's, which is not audited.
    "snug.accounts",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "snug.core",
    "snug.audit",
    "snug.documents",
    "snug.links",
    "snug.viewer",
    "snug.analytics",
    "snug.rooms",
    "snug.nda",
]

AUTH_USER_MODEL = "accounts.Admin"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:overview"
CSRF_FAILURE_VIEW = "snug.core.views.csrf_failure"

# argon2id only (Django's defaults: 100 MiB, two passes, eight lanes). With a
# single hasher there is no weaker algorithm for an old hash to fall back on.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Order matters. The header middleware comes first, so every response gets
# its headers, including the health check and static files, which are both
# answered before the rest of the stack runs.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "snug.core.middleware.extra_security_headers",
    "snug.core.middleware.health_check",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Before the CSRF check, which would otherwise read the whole upload first.
    "snug.documents.uploads.upload_size_limit",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "snug.accounts.sessions.session_limits",
]

ROOT_URLCONF = "snug.urls"
WSGI_APPLICATION = "snug.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "snug.core.context_processors.brand",
            ],
        },
    },
]

# SQLite in WAL mode, so readers never block the writer. IMMEDIATE takes the
# write lock when a transaction starts, so two writers queue (busy_timeout)
# instead of failing with "database is locked" halfway through.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": CONFIG.database_path,
        "OPTIONS": {
            "init_command": (
                "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000"
            ),
            "transaction_mode": "IMMEDIATE",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-gb"
USE_I18N = False
TIME_ZONE = "Europe/London"
USE_TZ = True

STATIC_URL = "/static/"
# Snug's own CSS, JavaScript, fonts, icons and logo. They belong to no single
# app, so they are listed here; collectstatic copies them into STATIC_ROOT.
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR.parent / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------- uploads
#
# Uploads larger than 2.5 MB stream to a temporary file in the data folder,
# on the same disk as the originals, so the finished file is moved, not copied.
FILE_UPLOAD_TEMP_DIR = CONFIG.data_dir / "tmp"
MAX_UPLOAD_BYTES = CONFIG.max_upload_mb * 1024 * 1024
MAX_VIDEO_BYTES = CONFIG.max_video_mb * 1024 * 1024

# ---------------------------------------------------------------- email
#
# Mail goes through the settings saved in Settings, or SMTP in snug.env. On
# localhost without either, it is written to <data>/outbox for development,
# never to the log: messages carry links and codes. Otherwise email is off
# and the admin pages say so.

DEFAULT_FROM_EMAIL = CONFIG.email_from or "snug@localhost"
# The transport reads the settings in force when each message is sent: those
# saved in Settings, else SMTP from snug.env, else the outbox on localhost.
MAILERS = {"default": {"BACKEND": "snug.core.email_backend.EmailBackend"}}

# ---------------------------------------------------------------- security
#
# Caddy terminates TLS and is the only thing that can reach the app, so the
# X-Forwarded-Proto header it sets can be trusted. Caddy also redirects HTTP.

if CONFIG.https:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # One year. Not includeSubDomains: the founder's other subdomains are not ours.
    SECURE_HSTS_SECONDS = 31_536_000

# __Host- cookies must be Secure, sent to this host only, for every path.
_cookie_prefix = "__Host-" if CONFIG.https else ""
SESSION_COOKIE_NAME = f"{_cookie_prefix}snug-session"
SESSION_COOKIE_SECURE = CONFIG.https
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 12 * 60 * 60
CSRF_COOKIE_NAME = f"{_cookie_prefix}snug-csrf"
CSRF_COOKIE_SECURE = CONFIG.https
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
# Viewer URLs carry link tokens, so no page may send a Referer to another site.
# Not "no-referrer": with it, browsers send "Origin: null" on form posts, which
# Django's CSRF check rightly refuses, and nobody could sign in.
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Strict CSP: nothing is allowed unless listed, and nothing inline, ever.
SECURE_CSP = {
    "default-src": [CSP.NONE],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    # Videos stream from Snug itself, through the same authorisation as pages.
    "media-src": [CSP.SELF],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "manifest-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "object-src": [CSP.NONE],
}

# Django's deployment checks, deliberately declined. Each reason must hold.
SILENCED_SYSTEM_CHECKS = [
    "security.W008",  # SECURE_SSL_REDIRECT: Caddy redirects every HTTP request first.
    "security.W005",  # HSTS includeSubDomains: the founder's other subdomains are not ours.
    "security.W021",  # HSTS preload: a hard-to-reverse choice for the founder's whole domain.
    # Email may be off until the operator sets up SMTP. The admin pages say so,
    # and `snug doctor` reports it, since viewers then cannot get codes.
    "mail.E001",
]

# ---------------------------------------------------------------- logging
#
# Logs go to standard output for Docker to collect. There is deliberately no
# request access log: viewer URLs carry link tokens, which must never be logged.
# An unexpected error is logged with its traceback (django.request), and every
# line passes through RedactSecrets, which blanks link and setup tokens.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "filters": {"redact": {"()": "snug.core.redaction.RedactSecrets"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain", "filters": ["redact"]}
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"level": "ERROR"},
        "django.server": {"level": "ERROR"},
        # Caddy forwards only the configured domain, so any other Host header
        # is a probe. Refuse it (Django answers 400) without a log traceback.
        "django.security.DisallowedHost": {"handlers": [], "propagate": False},
    },
}
