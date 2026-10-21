#!/bin/sh
set -e
python manage.py wait_for_services
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi
if [ "${SEED_DEMO:-0}" = "1" ]; then
  python manage.py seed_demo
fi
exec "$@"
