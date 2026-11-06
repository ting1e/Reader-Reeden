"""Run one watcher alongside the web service, sharing its database and mounts."""
import logging
import fcntl
import os
import queue
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import close_old_connections

from reader.models import UserSetting
from reader.services.s3_correction import process_file
from reader.services.s3_polling import poll_progress

logger = logging.getLogger('reader.s3_progress')


class Command(BaseCommand):
    help = '监听已启用用户的本地 book_progress 目录，纠正 S3 旧进度覆盖'

    def add_arguments(self, parser):
        parser.add_argument('--lock-fd', type=int, help='Inherited singleton lock descriptor')

    def handle(self, *args, **options):
        if options.get('lock_fd') is not None:
            lock = os.fdopen(options['lock_fd'], 'a+')
        else:
            local = Path(settings.BASE_DIR) / 'local'
            local.mkdir(parents=True, exist_ok=True)
            lock = (local / '.s3-progress-watcher.lock').open('a+')
        with lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write('S3 progress watcher already running.')
                return
            self.watch()
        # If a user enabled while this process was exiting, restart after unlock.
        from reader.services.s3_watcher import start_if_enabled
        start_if_enabled()

    def watch(self):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        events = queue.Queue()

        class Handler(FileSystemEventHandler):
            def on_closed(self, event):
                if not event.is_directory:
                    events.put(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    events.put(event.dest_path)

        observer = Observer()
        watches = {}
        users = {}
        pending = {}
        polls = {}

        def process(user_id, path):
            try:
                process_file(user_id, path)
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception('S3 progress observation failed user=%s file=%s', user_id, path)

        observer.start()
        self.stdout.write('S3 progress watcher started; waiting for enabled directories.')
        next_refresh = 0
        try:
            while True:
                now = time.monotonic()
                if now >= next_refresh:
                    close_old_connections()
                    desired = {}
                    enabled = list(UserSetting.objects.filter(s3_correction_enabled=True))
                    if not enabled:
                        # Release the lock before checking for a concurrent re-enable.
                        break
                    for setting in enabled:
                        if not setting.s3_progress_path:
                            continue
                        path = Path(setting.s3_progress_path)
                        if path.is_absolute() and path.is_dir():
                            desired.setdefault(str(path.resolve()), set()).add(setting.user_id)
                    for directory in set(watches) - set(desired):
                        observer.unschedule(watches.pop(directory))
                    for directory, ids in desired.items():
                        if directory not in watches:
                            watches[directory] = observer.schedule(Handler(), directory, recursive=False)
                        for user_id in ids - users.get(directory, set()):
                            for filename in Path(directory).glob('*.json'):
                                process(user_id, filename)
                    users = desired
                    polling_users = {s.user_id for s in enabled if not s.s3_progress_path}
                    for user_id in set(polls) - polling_users:
                        del polls[user_id]
                    for setting in enabled:
                        if setting.s3_progress_path:
                            continue
                        poll = polls.setdefault(setting.user_id, {'seen': {}, 'next': 0})
                        if time.monotonic() >= poll['next']:
                            try:
                                poll_progress(setting, poll['seen'])
                            except Exception:
                                logger.exception('S3 directory polling failed user=%s', setting.user_id)
                            poll['next'] = time.monotonic() + setting.s3_poll_seconds
                    next_refresh = now + 1
                try:
                    filename = events.get(timeout=0.1)
                    path = Path(filename)
                    if path.suffix == '.json':
                        pending[str(path.resolve())] = time.monotonic() + 0.2
                except queue.Empty:
                    pass
                for filename, deadline in list(pending.items()):
                    if time.monotonic() < deadline:
                        continue
                    del pending[filename]
                    for user_id in users.get(str(Path(filename).parent), set()):
                        process(user_id, filename)
        except KeyboardInterrupt:
            pass
        finally:
            observer.stop()
            observer.join()
            close_old_connections()
