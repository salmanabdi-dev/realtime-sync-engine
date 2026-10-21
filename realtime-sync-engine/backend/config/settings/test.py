from .base import *  # noqa: F401,F403
from .base import SYNC

DEBUG = False
ALLOWED_HOSTS = ["*"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Tests open many short-lived threads; don't keep their connections around.
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
SYNC["RATE_LIMIT_PER_SECOND"] = 10_000
