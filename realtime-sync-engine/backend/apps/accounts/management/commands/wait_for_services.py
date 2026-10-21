"""Block until PostgreSQL and Redis accept connections (used by the Docker entrypoint)."""

import time

import redis
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Wait for PostgreSQL and Redis to become available."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=60.0)

    def handle(self, *args, timeout, **options):
        deadline = time.monotonic() + timeout
        self._wait("PostgreSQL", self._check_db, deadline)
        if settings.CHANNEL_LAYER_BACKEND != "memory":
            self._wait("Redis", self._check_redis, deadline)

    def _wait(self, name, check, deadline):
        while True:
            try:
                check()
                self.stdout.write(f"{name} is available.")
                return
            except Exception as exc:  # noqa: BLE001
                if time.monotonic() > deadline:
                    raise CommandError(f"{name} unavailable: {exc}") from exc
                time.sleep(0.5)

    @staticmethod
    def _check_db():
        try:
            connection.ensure_connection()
        finally:
            connection.close()

    @staticmethod
    def _check_redis():
        redis.Redis.from_url(settings.REDIS_URL).ping()
