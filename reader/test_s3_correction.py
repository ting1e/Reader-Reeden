import json
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Book, Chapter, S3CorrectionState, UserSetting
from .services.s3_correction import capture_snapshot, position_words, process_file, should_correct


class CorrectionRuleTests(SimpleTestCase):
    def test_boundaries(self):
        a = {'time': '2026-01-01T10:00:00Z', 'words': 1000}
        b = {'time': '2026-01-01T10:10:00Z', 'words': 3000}
        s = {'data': {'lastReadTime': '2026-01-01T10:05:00Z'}, 'words': 4000}
        self.assertTrue(should_correct(a, b, s))
        self.assertFalse(should_correct(a, {**b, 'words': 3001}, s))
        self.assertFalse(should_correct(a, {**b, 'time': '2026-01-01T10:09:59Z'}, s))
        self.assertFalse(should_correct(a, b, {**s, 'words': 3000}))
        for timestamp in (a['time'], b['time']):
            self.assertFalse(should_correct(a, b, {**s, 'data': {'lastReadTime': timestamp}}))


class CorrectionTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.user = get_user_model().objects.create_user(username='correction-test')
        self.setting = UserSetting.objects.create(
            user_id=self.user.id, s3_correction_enabled=True,
            s3_progress_path=self.folder.name,
            s3_setting=json.dumps({'accessKeyId': 'test', 'secretAccessKey': 'test',
                                  'endpoint': 'https://example.invalid', 'bucket': 'test', 'prefix': 'reader'}))
        self.book = Book.objects.create(md5='a' * 32, word_count=20000)
        Chapter.objects.create(book_id=self.book.id, index=0, start=0, end=20000)
        self.path = Path(self.folder.name) / (self.book.md5 + '.json')
        self.cloud = Mock()
        self.text = patch('reader.services.s3_correction.read_book_text', return_value='字' * 20000)
        self.text.start()
        self.addCleanup(self.text.stop)
        self.client_patch = patch('reader.services.s3._get_s3_client', return_value=self.cloud)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def data(self, minute, words, device='remote'):
        return {'schemaVersion': 1, 'bookId': self.book.md5, 'deviceId': device,
                'lastReadTime': f'2026-01-01T10:{minute:02d}:00Z',
                'sectionIndex': 0, 'paragraphIndex': 0, 'elementIndex': words * 2,
                'readProgress': words // 2, 'todayStats': {'date': '2026-01-01'},
                'extension': 'preserved'}

    def upload(self, minute, words=1000, device='remote', baseline=False):
        self.path.write_text(json.dumps(self.data(minute, words, device)), encoding='utf-8')
        return process_file(self.user.id, self.path, baseline=baseline)

    def test_end_to_end_own_event_duplicates_and_quota(self):
        self.assertFalse(self.upload(0, baseline=True))
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.assertTrue(self.upload(10, 3000))
        result = json.loads(self.cloud.put_object.call_args.kwargs['Body'])
        self.assertEqual(result['elementIndex'], 16000)
        self.assertEqual(result['extension'], 'preserved')
        self.assertEqual(result['todayStats'], {'date': '2026-01-01'})
        self.assertNotEqual(result['lastReadTime'], self.data(5, 8000)['lastReadTime'])
        self.assertFalse(process_file(self.user.id, self.path))
        self.path.write_text(json.dumps(result), encoding='utf-8')
        self.assertFalse(process_file(self.user.id, self.path))
        self.assertEqual(self.cloud.put_object.call_count, 1)

        capture_snapshot(self.user.id, self.book, self.data(15, 8500, 'server'))
        self.assertTrue(self.upload(20, 3000))
        capture_snapshot(self.user.id, self.book, self.data(25, 9000, 'server'))
        self.assertFalse(self.upload(30, 3000))
        self.assertEqual(self.cloud.put_object.call_count, 2)
        state = S3CorrectionState.objects.get()
        original_time = state.snapshot['data']['lastReadTime']
        self.assertEqual(original_time, self.data(25, 9000)['lastReadTime'])
        S3CorrectionState.objects.update(window_start=timezone.now() - timedelta(minutes=5))
        capture_snapshot(self.user.id, self.book, self.data(35, 9500, 'server'))
        self.assertTrue(self.upload(40, 3000))
        self.assertEqual(S3CorrectionState.objects.get().correction_count, 1)

    def test_disabled_does_not_capture_or_upload(self):
        self.setting.s3_correction_enabled = False
        self.setting.save()
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.assertFalse(self.upload(10))
        self.assertFalse(S3CorrectionState.objects.exists())
        self.cloud.put_object.assert_not_called()

    def test_devices_are_separate_and_failed_upload_does_not_count(self):
        self.upload(0)
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.assertFalse(self.upload(10, device='other'))
        self.cloud.put_object.side_effect = RuntimeError('upload failed')
        with self.assertRaises(RuntimeError):
            self.upload(10)
        self.assertEqual(S3CorrectionState.objects.get().correction_count, 0)

    def test_volume_index_and_epub_absolute_text_position(self):
        chapter = Chapter.objects.get(book_id=self.book.id)
        chapter.volume_index = 0
        chapter.volume_title = '第一卷'
        chapter.save()
        payload = {**self.data(0, 2000), 'sectionIndex': 1}
        self.assertEqual(position_words(self.book, payload), 2000)
        self.book.format = 'epub'
        chapter.start, chapter.end = 4, 8
        with patch('reader.services.epub.read_cache', return_value={'text': '甲乙\n丙丁戊己庚'}), \
                patch('reader.services.epub.import_position', return_value=(chapter, 2, True)):
            self.assertEqual(position_words(self.book, payload), 5)

    def test_epub_correction_replaces_all_position_extensions(self):
        self.book.format = 'epub'
        self.book.save()
        with patch('reader.services.s3_correction.position_words',
                   side_effect=lambda book, data: data['elementIndex'] // 2):
            self.upload(0)
            snapshot = self.data(5, 8000, 'server')
            snapshot['readerEpub'] = {'position': {'sectionIndex': 0}, 'words': 8000}
            capture_snapshot(self.user.id, self.book, snapshot)
            self.assertTrue(self.upload(10))
        payload = json.loads(self.cloud.put_object.call_args.kwargs['Body'])
        self.assertEqual(payload['readerEpub'], snapshot['readerEpub'])
        self.assertEqual(payload['elementIndex'], snapshot['elementIndex'])

    @override_settings(ALLOWED_HOSTS=['testserver'])
    @patch('reader.services.s3_watcher.start_if_enabled')
    def test_settings_validation_and_disabled_path(self, start_watcher):
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        url = reverse('reader:user_settings_s3')
        values = {'s3_setting': self.setting.s3_setting, 's3_correction_settings': '1',
                  's3_correction_enabled': 'on', 's3_progress_path': 'relative/path'}
        self.assertFalse(self.client.post(url, values).json()['success'])
        start_watcher.assert_not_called()
        values['s3_progress_path'] = self.folder.name
        self.assertTrue(self.client.post(url, values).json()['success'])
        start_watcher.assert_called_once()
        values.pop('s3_correction_enabled')
        values['s3_progress_path'] = '/not/mounted/yet'
        self.assertTrue(self.client.post(url, values).json()['success'])
        self.setting.refresh_from_db()
        self.assertFalse(self.setting.s3_correction_enabled)
        self.assertEqual(self.setting.s3_progress_path, '/not/mounted/yet')


class LocalFileEventTests(SimpleTestCase):
    def test_inotify_close_and_atomic_replace(self):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        events = Queue()

        class Handler(FileSystemEventHandler):
            def on_closed(self, event):
                events.put(('closed', event.src_path))

            def on_moved(self, event):
                events.put(('moved', event.dest_path))

        with tempfile.TemporaryDirectory() as folder:
            observer = Observer()
            observer.schedule(Handler(), folder)
            observer.start()
            try:
                path = Path(folder) / 'progress.json'
                start = time.monotonic()
                path.write_text('{}')
                self.assertEqual(events.get(timeout=3), ('closed', str(path)))
                temporary = Path(folder) / 'upload.tmp'
                temporary.write_text('{"new":true}')
                self.assertEqual(events.get(timeout=3), ('closed', str(temporary)))
                temporary.replace(path)
                self.assertEqual(events.get(timeout=3), ('moved', str(path)))
                self.assertLess(time.monotonic() - start, 3)
            finally:
                observer.stop()
                observer.join()


class WatcherLifecycleTests(TestCase):
    def test_disabled_never_spawns(self):
        from .services.s3_watcher import start_if_enabled
        with patch('reader.services.s3_watcher.subprocess.Popen') as spawn:
            start_if_enabled()
        spawn.assert_not_called()

    def test_enabled_spawns_with_lock_and_duplicate_is_skipped(self):
        import fcntl
        from .services.s3_watcher import start_if_enabled
        UserSetting.objects.create(user_id=100, s3_correction_enabled=True)
        with tempfile.TemporaryDirectory() as folder, override_settings(BASE_DIR=Path(folder)), \
                patch('reader.services.s3_watcher.subprocess.Popen') as spawn:
            start_if_enabled()
            spawn.assert_called_once()
            self.assertIn('watch_s3_progress', spawn.call_args.args[0])
            lock_path = Path(folder) / 'local' / '.s3-progress-watcher.lock'
            with lock_path.open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                start_if_enabled()
            self.assertEqual(spawn.call_count, 1)

    def test_watcher_exits_when_last_user_disables(self):
        from reader.management.commands.watch_s3_progress import Command
        # No enabled users: the loop exits rather than staying alive indefinitely.
        with patch('watchdog.observers.Observer') as observer:
            Command().watch()
        observer.return_value.stop.assert_called_once()
        observer.return_value.join.assert_called_once()
