"""Shared settings. Environment-specific modules (dev/test/bench) import * from here."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend,backend-b").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "channels",
    "apps.accounts",
    "apps.documents",
    "apps.sync",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# --- PostgreSQL -------------------------------------------------------------
DB_POOL = env_bool("DB_POOL", False)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "sync_engine"),
        "USER": os.environ.get("POSTGRES_USER", "sync"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "sync"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Persistent connections avoid a TCP + auth handshake per mutation.
        # (Django's built-in pool requires CONN_MAX_AGE = 0.)
        "CONN_MAX_AGE": 0 if DB_POOL else int(os.environ.get("DB_CONN_MAX_AGE", "300")),
        "CONN_HEALTH_CHECKS": not DB_POOL,
        "OPTIONS": {"pool": {"min_size": 2, "max_size": 20}} if DB_POOL else {},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Redis / Channels -------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CHANNEL_LAYER_BACKEND = os.environ.get("CHANNEL_LAYER_BACKEND", "redis-pubsub")

if CHANNEL_LAYER_BACKEND == "memory":
    # Only for quick local experiments; never crosses process boundaries.
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
else:
    CHANNEL_LAYERS = {
        "default": {
            # PUBLISH/SUBSCRIBE based layer: lowest latency fan-out, no persistence.
            # Durability comes from PostgreSQL + version numbers, not from Redis.
            "BACKEND": "channels_redis.pubsub.RedisPubSubChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }

# --- REST -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# --- Sync engine ------------------------------------------------------------
SYNC = {
    # Conflicts further behind than this get a full snapshot instead of a replay.
    "MAX_CATCHUP_REVISIONS": int(os.environ.get("SYNC_MAX_CATCHUP", "200")),
    "MAX_DOCUMENT_CHARS": int(os.environ.get("SYNC_MAX_DOCUMENT_CHARS", "1000000")),
    "MAX_OPS_PER_MUTATION": 100,
    "MAX_INSERT_CHARS": 10000,
    # Messages per second per socket before the server answers with rate_limited.
    "RATE_LIMIT_PER_SECOND": int(os.environ.get("SYNC_RATE_LIMIT", "200")),
    # database_sync_to_async(thread_sensitive=...). False lets DB work for different
    # sockets run in parallel threads; correctness never depends on this flag.
    "DB_THREAD_SENSITIVE": env_bool("SYNC_DB_THREAD_SENSITIVE", True),
    "METRICS_ENABLED": env_bool("SYNC_METRICS_ENABLED", True),
}

AUTH_PASSWORD_VALIDATORS = []  # demo project: keep sign-up friction-free
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False}},
}
