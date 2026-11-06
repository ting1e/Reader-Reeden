"""Start the singleton watcher only when at least one user has enabled it."""
import fcntl
import logging
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from ..models import UserSetting

logger = logging.getLogger('reader')


def start_if_enabled():
    if not UserSetting.objects.filter(s3_correction_enabled=True).exists():
        return
    local = Path(settings.BASE_DIR) / 'local'
    local.mkdir(parents=True, exist_ok=True)
    # Acquire before spawning. The child inherits and holds this lock until exit.
    with (local / '.s3-progress-watcher.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        logs = local / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / 's3-progress-watcher.log').open('a') as output:
            subprocess.Popen(
                [sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'),
                 'watch_s3_progress', '--lock-fd', str(lock.fileno())],
                cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                pass_fds=(lock.fileno(),), start_new_session=True,
            )


def restore_watcher():
    """Web startup only, not AppConfig.ready (which also runs in the child)."""
    try:
        start_if_enabled()
    except Exception:
        # A fresh installation may not have tables until migrate has run.
        logger.exception('Unable to restore S3 progress watcher')
