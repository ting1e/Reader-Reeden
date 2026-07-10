#!/bin/sh
set -e

mkdir -p /app/local/logs

python manage.py migrate --noinput

exec "$@"
