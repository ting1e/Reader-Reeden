import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone as utc_timezone
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Book, Chapter, S3CorrectionState, UserSetting
from .services.s3_correction import capture_snapshot, process_file, should_correct


class CorrectionRuleTests(SimpleTestCase):
    def test_boundaries(self):
        read = {'lastReadTime': '2026-01-01T10:00:00Z'}
        modified = datetime(2026, 1, 1, 10, 5, tzinfo=utc_timezone.utc)
        snapshot = {'data': {'lastReadTime': '2026-01-01T10:01:00Z'}}
        self.assertFalse(should_correct(modified, read, snapshot))
        self.assertTrue(should_correct(modified + timedelta(microseconds=1), read, snapshot))
        self.assertFalse(should_correct(modified - timedelta(minutes=6), read, snapshot))
        self.assertFalse(should_correct(modified + timedelta(minutes=1), read, {'data': read}))
        self.assertTrue(should_correct(modified, read, snapshot, delay_minutes=4))


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
        self.client_patch = patch('reader.services.s3._get_s3_client', return_value=self.cloud)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def data(self, minute, words, device='remote'):
        return {'schemaVersion': 1, 'bookId': self.book.md5, 'deviceId': device,
                'lastReadTime': f'2026-01-01T10:{minute:02d}:00Z',
                'sectionIndex': 0, 'paragraphIndex': 0, 'elementIndex': words * 2,
                'readProgress': words // 2, 'todayStats': {'date': '2026-01-01'},
                'extension': 'preserved'}

    def upload(self, minute, words=1000, device='remote'):
        payload = self.data(max(0, minute - 10), words, device)
        self.path.write_text(json.dumps(payload), encoding='utf-8')
        timestamp = datetime(2026, 1, 1, 10, minute, tzinfo=utc_timezone.utc).timestamp()
        os.utime(self.path, (timestamp, timestamp))
        return process_file(self.user.id, self.path)

    def test_end_to_end_own_event_duplicates_and_quota(self):
        self.assertFalse(self.upload(0))
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        with self.assertLogs('reader.s3_progress', level='INFO') as logs:
            self.assertTrue(self.upload(10, 3000))
        success_logs = [line for line in logs.output if 'Corrected S3 progress' in line]
        self.assertEqual(len(success_logs), 1)
        self.assertIn(self.book.md5, success_logs[0])
        self.assertIn('old_position=', success_logs[0])
        self.assertIn('new_position=', success_logs[0])
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

    def test_custom_rules_and_quota(self):
        self.setting.s3_upload_delay_minutes = 1
        self.setting.s3_correction_window_minutes = 1
        self.setting.s3_correction_limit = 1
        self.setting.save()
        self.upload(0)
        capture_snapshot(self.user.id, self.book, self.data(1, 8000, 'server'))
        self.assertTrue(self.upload(2, 1020))
        capture_snapshot(self.user.id, self.book, self.data(3, 8500, 'server'))
        self.assertFalse(self.upload(4, 1020))
        S3CorrectionState.objects.update(window_start=timezone.now() - timedelta(minutes=1))
        capture_snapshot(self.user.id, self.book, self.data(5, 9000, 'server'))
        self.assertTrue(self.upload(6, 1020))

    def test_first_observation_can_correct_even_if_server_position_is_behind(self):
        capture_snapshot(self.user.id, self.book, self.data(5, 100, 'server'))
        self.assertTrue(self.upload(10, 18000))
        payload = json.loads(self.cloud.put_object.call_args.kwargs['Body'])
        self.assertEqual(payload['elementIndex'], 200)

    def test_same_content_with_new_mtime_is_checked_again(self):
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.assertFalse(self.upload(5, 1000))  # delay exactly five minutes
        timestamp = datetime(2026, 1, 1, 10, 6, tzinfo=utc_timezone.utc).timestamp()
        os.utime(self.path, (timestamp, timestamp))
        self.assertTrue(process_file(self.user.id, self.path))

    def test_remote_polling_without_local_directory(self):
        import io
        from botocore.exceptions import ClientError
        from .services.s3_polling import poll_progress
        self.setting.s3_progress_path = ''
        self.setting.save()
        key = f'reader/book_progress/{self.book.md5}.json'
        self.cloud.get_paginator.return_value.paginate.return_value = [
            {'Contents': [{'Key': key}, {'Key': 'reader/book_progress/not-json.txt'}]}]
        self.cloud.get_object.return_value = {
            'Body': io.BytesIO(json.dumps(self.data(0, 1000)).encode()), 'ETag': 'v1',
            'LastModified': datetime(2026, 1, 1, 10, 0, tzinfo=utc_timezone.utc)}
        seen = {}
        poll_progress(self.setting, seen)
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.cloud.get_object.return_value = {
            'Body': io.BytesIO(json.dumps(self.data(0, 1000)).encode()), 'ETag': 'v1',
            'LastModified': datetime(2026, 1, 1, 10, 10, tzinfo=utc_timezone.utc)}
        poll_progress(self.setting, seen)
        self.assertNotIn('IfNoneMatch', self.cloud.get_object.call_args.kwargs)
        self.cloud.put_object.assert_called_once()
        self.cloud.get_object.side_effect = ClientError(
            {'Error': {'Code': '304'}, 'ResponseMetadata': {'HTTPStatusCode': 304}}, 'GetObject')
        poll_progress(self.setting, seen)
        self.assertEqual(self.cloud.put_object.call_count, 1)

    def test_failed_upload_does_not_count(self):
        self.upload(0)
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.cloud.put_object.side_effect = RuntimeError('upload failed')
        with self.assertRaises(RuntimeError):
            self.upload(10)
        self.assertEqual(S3CorrectionState.objects.get().correction_count, 0)

    def test_volume_index_snapshot_export(self):
        chapter = Chapter.objects.get(book_id=self.book.id)
        chapter.volume_index = 0
        chapter.volume_title = '第一卷'
        chapter.save()
        capture_snapshot(self.user.id, self.book, self.data(5, 8000, 'server'))
        self.assertEqual(S3CorrectionState.objects.get().snapshot['data']['sectionIndex'], 1)

    def test_epub_correction_replaces_all_position_extensions(self):
        self.book.format = 'epub'
        self.book.save()
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

    @override_settings(ALLOWED_HOSTS=['testserver'])
    @patch('reader.services.s3_watcher.start_if_enabled')
    def test_empty_directory_and_configurable_values(self, start_watcher):
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        values = {'s3_setting': self.setting.s3_setting, 's3_correction_settings': '1',
                  's3_correction_enabled': 'on', 's3_progress_path': '',
                   's3_poll_seconds': '15', 's3_upload_delay_minutes': '3',
                  's3_correction_window_minutes': '2',
                  's3_correction_limit': '4'}
        url = reverse('reader:user_settings_s3')
        self.assertTrue(self.client.post(url, values).json()['success'])
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.s3_progress_path, '')
        self.assertEqual(self.setting.s3_poll_seconds, 15)
        self.assertEqual(self.setting.s3_correction_limit, 4)
        values.pop('s3_setting')
        values['s3_poll_seconds'] = '2'
        response = self.client.post(url, values).json()
        self.assertTrue(response['success'])
        self.assertEqual(response['msg'], '进度纠正设置已保存')
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.s3_poll_seconds, 2)
        original_s3 = self.setting.s3_setting
        self.assertTrue(self.client.post(url, {'s3_setting': original_s3}).json()['success'])
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.s3_poll_seconds, 2)
        self.assertTrue(self.setting.s3_correction_enabled)
        values['s3_poll_seconds'] = '0'
        self.assertFalse(self.client.post(url, values).json()['success'])


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
