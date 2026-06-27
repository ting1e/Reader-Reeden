#!/bin/sh
set -e

mkdir -p /app/local/books /app/local/upload /app/local/book_progress /app/local/fonts /app/local/logs

python manage.py migrate --noinput

exec "$@"
