import hashlib
import io
import json
import os
import tempfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Book, Chapter, KosyncProgress, SystemSetting, UserSetting
from .services import kosync
from .services.kosync_document import document_id, filename_checksum, partial_md5_checksum
from .services.kosync_position import (
    koreader_progress_to_txt, txt_to_koreader_progress,
)
from .utils import get_or_create_user_setting


class KosyncApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='kosync-user', password='secret', is_superuser=True,
        )
        self.other = get_user_model().objects.create_user(
            username='kosync-other', password='secret',
        )
        self.setting = get_or_create_user_setting(self.user)
        kosync.set_auth_hash(self.user, 'secret')
        kosync.set_auth_hash(self.other, 'secret')
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': True})
        self.headers = {
            'HTTP_X_AUTH_USER': self.user.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('secret'),
        }

    def put(self, payload, user=None):
        headers = self.headers if user is None else {
            'HTTP_X_AUTH_USER': user.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('secret'),
        }
        return self.client.put(
            reverse('reader:kosync_progress'), data=json.dumps(payload),
            content_type='application/json', **headers)

    def test_healthcheck_and_disabled_server(self):
        self.assertEqual(self.client.get(reverse('reader:kosync_healthcheck')).json(), {'state': 'OK'})
        self.assertEqual(self.client.get('/health').json(), {'state': 'OK'})

    def test_request_log_contains_client_ip(self):
        with self.assertLogs('reader.requests', level='INFO') as captured:
            self.client.get('/health', HTTP_X_FORWARDED_FOR='203.0.113.5, 10.0.0.1',
                            HTTP_USER_AGENT='probe/1.0')
        self.assertIn('ip=203.0.113.5', captured.output[-1])
        self.assertIn('ua=probe/1.0', captured.output[-1])
        SystemSetting.objects.filter(pk=1).update(kosync_enabled=False)
        response = self.client.get(reverse('reader:kosync_auth'), **self.headers)
        self.assertEqual(response.status_code, 503)
        response = self.put({'document': 'x', 'progress': 'p', 'percentage': 0})
        self.assertEqual(response.status_code, 503)

    def test_auth_and_closed_registration(self):
        response = self.client.get(reverse('reader:kosync_auth'), **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'authorized': 'OK'})
        bad = dict(self.headers, HTTP_X_AUTH_KEY='wrong')
        response = self.client.get(reverse('reader:kosync_auth'), **bad)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 2001)
        response = self.client.post(reverse('reader:kosync_create_user'), **self.headers)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()['code'], 2005)

    def test_progress_roundtrip_and_isolation(self):
        payload = {
            'document': '8b03a82761fae0ee6cd5a23700361e74',
            'progress': '/body/DocFragment[15]/body/p[22]/text().41',
            'percentage': 0.2082,
            'device': 'BOOX',
            'device_id': 'device-1',
            'metadata': {'title': 'Book'},
        }
        response = self.put(payload)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()['timestamp'], int)
        response = self.client.get(
            reverse('reader:kosync_get_progress', args=[payload['document']]), **self.headers)
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['progress'], payload['progress'])
        self.assertAlmostEqual(data['percentage'], payload['percentage'])
        self.assertEqual(data['device'], 'BOOX')
        self.assertEqual(data['device_id'], 'device-1')
        self.assertIsInstance(data['timestamp'], int)

        # Missing metadata on a later PUT must not erase the stored value.
        second = dict(payload, progress='later', percentage=0.5)
        second.pop('metadata')
        self.put(second)
        record = KosyncProgress.objects.get(user=self.user, document=payload['document'])
        self.assertEqual(record.metadata, {'title': 'Book'})
        self.assertEqual(record.progress, 'later')

        other_headers = {
            'HTTP_X_AUTH_USER': self.other.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('secret'),
        }
        data = self.client.get(
            reverse('reader:kosync_get_progress', args=[payload['document']]),
            **other_headers).json()
        self.assertEqual(data, {})

    def test_missing_progress_is_empty_200(self):
        response = self.client.get(
            reverse('reader:kosync_get_progress', args=['missing']), **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_invalid_put(self):
        response = self.put({'document': 'x', 'progress': '', 'percentage': 0})
        self.assertEqual(response.status_code, 400)
        response = self.put({'document': 'x', 'progress': 'p', 'percentage': 2})
        self.assertEqual(response.status_code, 400)
        response = self.put({'document': 'x' * 129, 'progress': 'p', 'percentage': 0})
        self.assertEqual(response.status_code, 400)

    def test_auth_key_rejected_after_password_change(self):
        self.user.set_password('new-secret')
        self.user.save()
        response = self.client.get(reverse('reader:kosync_auth'), **self.headers)
        self.assertEqual(response.status_code, 401)
        kosync.set_auth_hash(self.user, 'new-secret')
        headers = {
            'HTTP_X_AUTH_USER': self.user.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('new-secret'),
        }
        response = self.client.get(reverse('reader:kosync_auth'), **headers)
        self.assertEqual(response.status_code, 200)

    def test_auth_failures_are_rate_limited(self):
        for _ in range(5):
            self.client.get(reverse('reader:kosync_auth'), REMOTE_ADDR='10.9.9.9',
                            HTTP_X_AUTH_USER='nobody', HTTP_X_AUTH_KEY='bad')
        response = self.client.get(reverse('reader:kosync_auth'), REMOTE_ADDR='10.9.9.9',
                                   **self.headers)
        self.assertEqual(response.status_code, 429)


class KosyncDocumentTests(TestCase):
    def test_binary_and_filename(self):
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'a' * 2000 + b'b' * 5000)
            self.assertEqual(len(partial_md5_checksum(path)), 32)
            with open(path, 'rb') as stream:
                full = hashlib.md5(stream.read()).hexdigest()
            self.assertNotEqual(partial_md5_checksum(path), full)
            self.assertEqual(filename_checksum(path),
                             hashlib.md5(os.path.basename(path).encode()).hexdigest())
            self.assertEqual(document_id(path, 'filename'), filename_checksum(path))
            self.assertEqual(document_id(path, 'binary'), partial_md5_checksum(path))
        finally:
            os.remove(path)


class KosyncTxtPositionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='txt-kosync')
        self.data = '第一段内容\n第二段内容\n第三段内容\n'
        fd, self.path = tempfile.mkstemp(suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(self.data)
        self.book = Book.objects.create(
            book_url=self.path, name='txt', author='', intro='', rule='',
            first_chapter_title='', last_chapter_title='', charset='utf-8',
            word_count=len(self.data),
        )
        self.chapter = Chapter.objects.create(
            book_id=self.book.id, book_url=self.path, index=0, title='第一章',
            start=0, end=len(self.data),
        )

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_txt_xpointer_text_index_and_roundtrip(self):
        words = len('第一段内容第二段')
        position = txt_to_koreader_progress(self.book, self.chapter, words)
        self.assertIn('/body/p[2]/text().', position['progress'])
        chapter, restored, exact = koreader_progress_to_txt(
            self.book, position['progress'], position['percentage'])
        self.assertTrue(exact)
        self.assertEqual(chapter.id, self.chapter.id)
        self.assertEqual(restored, words)
        # KOReader also emits text()[1].N; accept that form and nested paths.
        chapter, restored, exact = koreader_progress_to_txt(
            self.book, '/body/DocFragment[1]/body/div/section/p[2]/text()[1].4', 0)
        self.assertTrue(exact)
        self.assertEqual(chapter.id, self.chapter.id)
        self.assertEqual(restored, len('第一段内容第二段内'))

    def test_txt_approximate_fallback(self):
        chapter, words, exact = koreader_progress_to_txt(
            self.book, '/body/DocFragment[1]/body/p[999]/text().0', 0.5)
        self.assertFalse(exact)
        self.assertEqual(chapter.id, self.chapter.id)
        self.assertGreater(words, 0)

    def test_internal_update_and_restore(self):
        setting = get_or_create_user_setting(self.user)
        setting.progress_sync_mode = 'kosync'
        setting.save(update_fields=['progress_sync_mode'])
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': True})
        words = len('第一段内容第二段')
        with patch('reader.services.kosync.book_document_id', return_value='doc1'), \
                patch('reader.services.kosync.get_device_id', return_value='json-device-id'):
            record = kosync.update_book_progress(self.user, self.book, self.chapter, words)
            self.assertEqual(record.document, 'doc1')
            self.assertEqual(record.device_id, 'json-device-id')
            self.assertIn('/body/p[2]/text().', record.progress)
            chapter, restored, exact = kosync.restore_txt_progress(self.user, self.book, record)
            self.assertTrue(exact)
            self.assertEqual(chapter.id, self.chapter.id)
            self.assertEqual(restored, words)
            # Same position is not uploaded again.
            again = kosync.update_book_progress(self.user, self.book, self.chapter, words)
            self.assertEqual(again.updated_at, record.updated_at)
            # A changed position is uploaded immediately (no 25s drop).
            later = kosync.update_book_progress(self.user, self.book, self.chapter, words + 1)
            self.assertNotEqual(later.progress, record.progress)


class KosyncReaderIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='kosync-reader', password='secret', is_superuser=True,
        )
        setting = get_or_create_user_setting(self.user)
        setting.progress_sync_mode = 'kosync'
        setting.save(update_fields=['progress_sync_mode'])
        kosync.set_auth_hash(self.user, 'secret')
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': True})
        self.folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.folder, ignore_errors=True))
        for target in ('reader.services.progress.get_progress_dir',
                       'reader.views.reader.get_progress_dir'):
            patcher = patch(target, return_value=self.folder)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('reader.services.progress.get_device_id', return_value='test-device')
        patcher.start()
        self.addCleanup(patcher.stop)

        data = '第一章内容\n第二段\n第二章内容\n第二段\n'
        fd, self.path = tempfile.mkstemp(suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
        self.book = Book.objects.create(
            book_url=self.path, name='kosync-reader', author='', intro='', rule='',
            first_chapter_title='', last_chapter_title='', charset='utf-8',
            uploader=self.user.id, word_count=len(data), md5='test-md5',
        )
        self.first = Chapter.objects.create(
            book_id=self.book.id, book_url=self.path, index=0, title='第一章',
            start=0, end=data.index('第二章'),
        )
        self.second = Chapter.objects.create(
            book_id=self.book.id, book_url=self.path, index=1, title='第二章',
            start=data.index('第二章'), end=len(data),
        )
        self.book.first_chapter_id = self.first.id
        self.book.last_chapter_id = self.second.id
        self.book.save(update_fields=['first_chapter_id', 'last_chapter_id'])
        self.client.force_login(self.user)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_save_and_restore_from_kosync(self):
        response = self.client.post(reverse('reader:book_view'), {
            'book_id': self.book.id, 'chapter_id': self.first.id, 'words': 3,
        })
        self.assertTrue(response.json()['success'])
        record = KosyncProgress.objects.get(user=self.user)
        self.assertEqual(record.document, kosync.book_document_id(self.book))

        # Simulate another device: remove local/DB position, keep KoSync.
        from .models import UserBookRecord
        UserBookRecord.objects.filter(user_id=self.user.id, book_id=self.book.id).delete()
        for name in os.listdir(self.folder):
            os.remove(os.path.join(self.folder, name))
        remote = txt_to_koreader_progress(self.book, self.second, 6)
        record.progress = remote['progress']
        record.percentage = remote['percentage']
        record.save(update_fields=['progress', 'percentage', 'updated_at'])

        response = self.client.get(reverse('reader:book_view'), {'book_id': self.book.id})
        self.assertEqual(response.context['chapter_id'], self.second.id)
        self.assertEqual(response.context['last_words'], 6)
        self.assertTrue(UserBookRecord.objects.filter(
            user_id=self.user.id, book_id=self.book.id, chapter_id=self.second.id).exists())
        # Reopening must not rewrite the stored KoSync XPointer.
        record.refresh_from_db()
        progress, updated_at = record.progress, record.updated_at
        self.client.get(reverse('reader:book_view'), {'book_id': self.book.id})
        record.refresh_from_db()
        self.assertEqual((record.progress, record.updated_at), (progress, updated_at))


    def test_save_falls_back_to_s3_when_server_disabled(self):
        SystemSetting.objects.filter(pk=1).update(kosync_enabled=False)
        with patch('reader.views.reader.sync_progress_to_s3') as sync_s3, \
                patch('reader.views.reader.kosync.update_book_progress') as update_kosync:
            response = self.client.post(reverse('reader:book_view'), {
                'book_id': self.book.id, 'chapter_id': self.first.id, 'words': 3,
            })
        self.assertTrue(response.json()['success'])
        sync_s3.assert_called_once()
        update_kosync.assert_not_called()
        self.assertFalse(KosyncProgress.objects.filter(user=self.user).exists())

    def test_restore_falls_back_to_s3_when_server_disabled(self):
        from .models import UserBookRecord
        SystemSetting.objects.filter(pk=1).update(kosync_enabled=False)
        UserBookRecord.objects.filter(user_id=self.user.id, book_id=self.book.id).delete()
        for name in os.listdir(self.folder):
            os.remove(os.path.join(self.folder, name))
        remote = {
            'schemaVersion': 1, 'bookId': self.book.md5,
            'sectionIndex': 1, 'paragraphIndex': 0, 'elementIndex': 0,
            'readProgress': 10000, 'lastReadTime': '2099-01-01T00:00:00Z',
        }
        cloud = Mock()
        cloud.get_object.return_value = {'Body': io.BytesIO(json.dumps(remote).encode())}
        cfg = {'bucket': 'test', 'prefix': 'test/'}
        with patch('reader.views.reader.get_s3_config', return_value=cfg), \
                patch('reader.views.reader._get_s3_client', return_value=cloud), \
                patch('reader.views.reader.kosync.book_record') as book_record:
            response = self.client.get(reverse('reader:book_view'), {'book_id': self.book.id})
        book_record.assert_not_called()
        cloud.get_object.assert_called_once()
        self.assertEqual(response.context['chapter_id'], self.second.id)


class KosyncSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='kosync-settings', password='secret', is_superuser=True,
        )
        get_or_create_user_setting(self.user)
        self.client.force_login(self.user)
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': True})

    def test_superuser_can_toggle_server(self):
        SystemSetting.objects.filter(pk=1).update(kosync_enabled=False)
        response = self.client.post(reverse('reader:user_settings_kosync_server'), {
            'kosync_enabled': 'on',
        })
        self.assertTrue(response.json()['success'])
        self.assertTrue(SystemSetting.load().kosync_enabled)
        response = self.client.post(reverse('reader:user_settings_kosync_server'), {})
        self.assertTrue(response.json()['success'])
        self.assertFalse(SystemSetting.load().kosync_enabled)

    def test_non_superuser_sees_server_status_only(self):
        normal = get_user_model().objects.create_user(username='plain-user', password='secret')
        self.client.force_login(normal)
        response = self.client.get(reverse('reader:user_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="kosync-server-toggle"')
        self.assertContains(response, '服务由超级管理员管理')

    def test_non_superuser_cannot_toggle_server(self):
        normal = get_user_model().objects.create_user(username='normal-user', password='secret')
        self.client.force_login(normal)
        response = self.client.post(reverse('reader:user_settings_kosync_server'), {
            'kosync_enabled': 'on',
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SystemSetting.load().kosync_enabled)

    def test_login_initializes_kosync_auth(self):
        self.client.logout()
        response = self.client.post(reverse('reader:login'), {
            'username': self.user.username,
            'password': 'secret',
        })
        self.assertEqual(response.content, b'success')
        setting = UserSetting.objects.get(user_id=self.user.id)
        self.assertTrue(setting.kosync_auth_hash)
        self.assertEqual(setting.kosync_password_version, kosync.password_version(self.user))
        from django.test import RequestFactory
        request = RequestFactory().get('/users/auth', **{
            'HTTP_X_AUTH_USER': self.user.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('secret'),
        })
        self.assertTrue(kosync.verified_user(request))

    def test_login_refreshes_kosync_auth_after_password_change(self):
        kosync.set_auth_hash(self.user, 'secret')
        self.user.set_password('new-secret')
        self.user.save()
        self.client.logout()
        response = self.client.post(reverse('reader:login'), {
            'username': self.user.username,
            'password': 'new-secret',
        })
        self.assertEqual(response.content, b'success')
        from django.test import RequestFactory
        request = RequestFactory().get('/users/auth', **{
            'HTTP_X_AUTH_USER': self.user.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('new-secret'),
        })
        self.assertTrue(kosync.verified_user(request))

    def test_save_mode_after_auth_initialized(self):
        kosync.set_auth_hash(self.user, 'secret')

        response = self.client.post(reverse('reader:user_settings_progress_sync'), {
            'progress_sync_mode': 'kosync',
            'kosync_checksum_method': 'filename',
        })
        self.assertTrue(response.json()['success'])
        setting = UserSetting.objects.get(user_id=self.user.id)
        self.assertEqual(setting.progress_sync_mode, 'kosync')
        self.assertEqual(setting.kosync_checksum_method, 'filename')

    def test_settings_page_prompts_relogin_when_auth_missing(self):
        response = self.client.get(reverse('reader:user_settings'))
        self.assertFalse(response.context['kosync_auth_ready'])
        self.assertContains(response, '当前登录会话尚未初始化 KoSync 认证')

    def test_auth_ready_rejects_stale_password_version(self):
        kosync.set_auth_hash(self.user, 'secret')
        self.assertTrue(kosync.auth_ready(self.user))
        self.user.set_password('new-secret')
        self.user.save()
        self.assertFalse(kosync.auth_ready(self.user))
        # Password changes invalidate the session hash; restore it to inspect the page.
        self.client.force_login(self.user)
        response = self.client.get(reverse('reader:user_settings'))
        self.assertFalse(response.context['kosync_auth_ready'])
        self.assertContains(response, '当前登录会话尚未初始化 KoSync 认证')

    def test_settings_page_hides_relogin_prompt_when_ready(self):
        kosync.set_auth_hash(self.user, 'secret')
        response = self.client.get(reverse('reader:user_settings'))
        self.assertTrue(response.context['kosync_auth_ready'])
        self.assertNotContains(response, '当前登录会话尚未初始化 KoSync 认证')

    def test_kosync_mode_requires_server_and_auth(self):
        response = self.client.post(reverse('reader:user_settings_progress_sync'), {
            'progress_sync_mode': 'kosync',
            'kosync_checksum_method': 'binary',
        })
        self.assertFalse(response.json()['success'])
        self.assertIn('认证', response.json()['error'])
        kosync.set_auth_hash(self.user, 'secret')
        SystemSetting.objects.filter(pk=1).update(kosync_enabled=False)
        response = self.client.post(reverse('reader:user_settings_progress_sync'), {
            'progress_sync_mode': 'kosync',
            'kosync_checksum_method': 'binary',
        })
        self.assertFalse(response.json()['success'])
        self.assertIn('未开启', response.json()['error'])
