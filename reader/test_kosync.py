import hashlib
import json
import os
import tempfile
from unittest.mock import patch

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

    def test_txt_roundtrip(self):
        words = len('第一段内容第二段')
        position = txt_to_koreader_progress(self.book, self.chapter, words)
        self.assertIn('/body/p[2]/text().', position['progress'])
        chapter, restored, exact = koreader_progress_to_txt(
            self.book, position['progress'], position['percentage'])
        self.assertTrue(exact)
        self.assertEqual(chapter.id, self.chapter.id)
        self.assertEqual(restored, words)

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
        with patch('reader.services.kosync.book_document_id', return_value='doc1'):
            record = kosync.update_book_progress(self.user, self.book, self.chapter, words, force=True)
            self.assertEqual(record.document, 'doc1')
            self.assertIn('/body/p[2]/text().', record.progress)
            chapter, restored, exact = kosync.restore_txt_progress(self.user, self.book, record)
            self.assertTrue(exact)
            self.assertEqual(chapter.id, self.chapter.id)
            self.assertEqual(restored, words)
            # Same position is not uploaded again.
            again = kosync.update_book_progress(self.user, self.book, self.chapter, words, force=True)
            self.assertEqual(again.updated_at, record.updated_at)


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


class KosyncSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='kosync-settings', password='secret', is_superuser=True,
        )
        get_or_create_user_setting(self.user)
        self.client.force_login(self.user)
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': True})

    def test_initialize_auth_and_mode(self):
        response = self.client.post(reverse('reader:user_settings_kosync_auth'), {
            'password': 'secret',
        })
        self.assertTrue(response.json()['success'])
        setting = UserSetting.objects.get(user_id=self.user.id)
        self.assertTrue(setting.kosync_auth_hash)
        from django.test import RequestFactory
        request = RequestFactory().get('/users/auth', **{
            'HTTP_X_AUTH_USER': self.user.username,
            'HTTP_X_AUTH_KEY': kosync.md5_hex('secret'),
        })
        self.assertTrue(kosync.verified_user(request))

        response = self.client.post(reverse('reader:user_settings_progress_sync'), {
            'progress_sync_mode': 'kosync',
            'kosync_checksum_method': 'filename',
        })
        self.assertTrue(response.json()['success'])
        setting.refresh_from_db()
        self.assertEqual(setting.progress_sync_mode, 'kosync')
        self.assertEqual(setting.kosync_checksum_method, 'filename')

    def test_kosync_mode_requires_server_and_auth(self):
        response = self.client.post(reverse('reader:user_settings_progress_sync'), {
            'progress_sync_mode': 'kosync',
            'kosync_checksum_method': 'binary',
        })
        self.assertFalse(response.json()['success'])
        self.assertIn('认证', response.json()['error'])
        _ = self.client.post(reverse('reader:user_settings_kosync_auth'), {'password': 'secret'})
        SystemSetting.objects.filter(pk=1).update(kosync_enabled=False)
        response = self.client.post(reverse('reader:user_settings_progress_sync'), {
            'progress_sync_mode': 'kosync',
            'kosync_checksum_method': 'binary',
        })
        self.assertFalse(response.json()['success'])
        self.assertIn('未开启', response.json()['error'])
