"""Run one watcher alongside the web service, sharing its database and mounts."""
import logging
import queue
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from reader.models import UserSetting
from reader.services.s3_correction import process_file

logger = logging.getLogger('reader')


class Command(BaseCommand):
    help = '监听已启用用户的本地 book_progress 目录，纠正 S3 旧进度覆盖'

    def handle(self, *args, **options):
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

        def process(user_id, path, baseline=False):
            try:
                process_file(user_id, path, baseline=baseline)
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
                    for setting in UserSetting.objects.filter(s3_correction_enabled=True):
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
                                process(user_id, filename, baseline=True)
                    users = desired
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
