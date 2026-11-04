import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Book, BookList, BookListItem, Chapter, KosyncProgress, SystemSetting, UserBookRecord
from .services import kosync
from .services.book_parser import (
    EPUB_VOLUME_VERSION, _assign_epub_volumes, _epub_volume_nodes, ensure_epub_volumes,
)
from .services.epub import EpubError, export_position, import_position, parse_epub, read_cache
from .services.kosync_position import epub_to_koreader_progress
from .services.progress import save_progress_json
from .services.s3 import sync_progress_to_s3
from .utils import get_or_create_user_setting, read_book_text


def sample_epub(version=3, unsafe=False, nested=False):
    output = io.BytesIO()
    nav_item = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>' if version == 3 else ''
    if nested:
        nav_toc = '''<nav epub:type="toc"><ol>
          <li><span>第一卷</span><ol><li><a href="body.xhtml#one">第一章</a></li></ol></li>
          <li><span>第二卷</span><ol><li><a href="body.xhtml#two">第二章</a></li></ol></li>
        </ol></nav>'''
        ncx_map = '''<navMap>
          <navPoint><navLabel><text>第一卷</text></navLabel><content src="body.xhtml#one"/>
            <navPoint><navLabel><text>第一章</text></navLabel><content src="body.xhtml#one"/></navPoint>
          </navPoint>
          <navPoint><navLabel><text>第二卷</text></navLabel><content src="body.xhtml#two"/>
            <navPoint><navLabel><text>第二章</text></navLabel><content src="body.xhtml#two"/></navPoint>
          </navPoint>
        </navMap>'''
    else:
        nav_toc = '<nav epub:type="toc"><ol><li><a href="body.xhtml#one">第一章</a></li><li><a href="body.xhtml#two">第二章</a></li></ol></nav>'
        ncx_map = '<navMap><navPoint><navLabel><text>第一章</text></navLabel><content src="body.xhtml#one"/></navPoint><navPoint><navLabel><text>第二章</text></navLabel><content src="body.xhtml#two"/></navPoint></navMap>'
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>''')
        archive.writestr('OEBPS/book.opf', '''<package xmlns="http://www.idpf.org/2007/opf" version="%s.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试 EPUB</dc:title><dc:creator>作者</dc:creator></metadata><manifest>
          <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
          <item id="body" href="body.xhtml" media-type="application/xhtml+xml"/>
          %s
          <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
          </manifest><spine toc="ncx"><itemref idref="cover"/><itemref idref="body"/></spine></package>''' % (version, nav_item))
        archive.writestr('OEBPS/cover.xhtml', '<html><body><img src="remote.png"/></body></html>')
        archive.writestr('OEBPS/body.xhtml', '''<!DOCTYPE html><html><head><title>不要读这个</title></head><body><h1 id="one">第一章</h1><p>中文 English 😀 &amp; 正文。</p><script>不执行脚本</script><h2 id="two">第二章</h2><p>第二段正文。</p></body></html>''')
        archive.writestr('OEBPS/nav.xhtml', '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>%s</body></html>''' % nav_toc)
        archive.writestr('OEBPS/toc.ncx', '''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">%s</ncx>''' % ncx_map)
        if unsafe:
            archive.writestr('../escape.txt', 'unsafe')
    return output.getvalue()


class EpubVolumeRecognitionTests(TestCase):
    def test_chapters_with_subsections_are_not_volumes(self):
        tree = [
            {'title': '第一章', 'start': 0, 'children': [
                {'title': '第一节', 'start': 0, 'children': []},
                {'title': '第二节', 'start': 50, 'children': []},
            ]},
            {'title': '第二章', 'start': 100, 'children': [
                {'title': '第一节', 'start': 100, 'children': []},
            ]},
        ]
        self.assertEqual(_epub_volume_nodes(tree), [])
        self.assertEqual(_assign_epub_volumes(
            [{'title': '第一章', 'start': 0}, {'title': '第二章', 'start': 100}], tree,
        ), [(None, ''), (None, '')])

    def test_supported_volume_title_formats(self):
        titles = [
            '第一卷', '第1卷', '第2部', '第3篇', '卷一', '卷二',
            '第一卷 风起', '上卷', '中卷', '下卷', '终卷',
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(_epub_volume_nodes([
                    {'title': title, 'start': 0, 'children': []},
                ]), [(0, title)])

    def test_chapter_titles_are_not_volumes(self):
        for title in ('第一章', '第1章', '第一节', '第1节', '正文', '前言', '楔子'):
            with self.subTest(title=title):
                self.assertEqual(_epub_volume_nodes([
                    {'title': title, 'start': 0, 'children': [
                        {'title': '第一节', 'start': 0, 'children': []},
                    ]},
                ]), [])

    def test_wrapper_node_keeps_inner_volumes(self):
        tree = [{
            'title': '正文', 'start': 0, 'children': [
                {'title': '第一卷', 'start': 0, 'children': [
                    {'title': '第一章', 'start': 0, 'children': []},
                ]},
                {'title': '第二卷', 'start': 100, 'children': [
                    {'title': '第二章', 'start': 100, 'children': []},
                ]},
            ],
        }]
        self.assertEqual([title for _, title in _epub_volume_nodes(tree)],
                         ['第一卷', '第二卷'])
        chapters = [
            {'title': '第一章', 'start': 0},
            {'title': '第二章', 'start': 100},
        ]
        self.assertEqual(_assign_epub_volumes(chapters, tree),
                         [(0, '第一卷'), (1, '第二卷')])

    def test_outer_volume_wins_over_nested_volume(self):
        tree = [{
            'title': '第一卷', 'start': 0, 'children': [
                {'title': '第一篇', 'start': 0, 'children': [
                    {'title': '第一章', 'start': 0, 'children': []},
                ]},
            ],
        }]
        self.assertEqual([title for _, title in _epub_volume_nodes(tree)], ['第一卷'])


class EpubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='epub-owner', is_superuser=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix='epub-tests-', dir='/tmp/opencode' if Path('/tmp/opencode').is_dir() else None,
        )
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.client.force_login(self.user)
        for name in ('get_upload_dir', 'get_local_books_dir', 'get_progress_dir'):
            patcher = patch('reader.views.books.' + name, return_value=str(self.folder))
            patcher.start()
            self.addCleanup(patcher.stop)
        for module in ('reader.services.progress', 'reader.services.epub_progress', 'reader.services.s3'):
            patcher = patch(module + '.get_progress_dir', return_value=str(self.folder))
            patcher.start()
            self.addCleanup(patcher.stop)

    def upload(self, blob=None, name='sample.epub'):
        response = self.client.post(reverse('reader:upload_file'), {
            'file': SimpleUploadedFile(name, blob if blob is not None else sample_epub()),
        })
        self.assertTrue(response.json()['success'], response.content)
        return Book.objects.get(file_name=name)

    def test_import_read_search_progress_and_delete(self):
        book = self.upload()
        self.assertEqual((book.format, book.name, book.author), ('epub', '测试 EPUB', '作者'))
        text = read_book_text(book)
        self.assertIn('中文 English 😀 & 正文。', text)
        self.assertNotIn('不执行脚本', text)
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertEqual([ch.title for ch in chapters], ['第一章', '第二章'])
        self.assertEqual(read_cache(book)['sections'][0]['paragraphs'], [])
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertContains(response, 'id="epub-frame"')
        resource = self.client.get(reverse('reader:epub_resource', args=[book.id]), {'path': 'OEBPS/body.xhtml'})
        self.assertContains(resource, '中文 English 😀 &amp; 正文。')
        self.assertNotContains(resource, '<script')
        self.client.post(reverse('reader:book_view'), {'book_id': book.id, 'chapter_id': chapters[1].id, 'words': 4})
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertEqual(response.context['chapter_id'], chapters[1].id)
        self.assertEqual(response.context['last_words'], 4)
        bookmark = self.client.post(reverse('reader:bookmark_save'), {
            'book_id': book.id, 'chapter_id': chapters[1].id, 'words_read': 4,
            'chapter_title': chapters[1].title, 'content': '第二段正文',
        })
        self.assertEqual(bookmark.status_code, 200)
        self.assertContains(self.client.get(reverse('reader:bookmark_list', args=[book.id])), '第二段正文')
        search = self.client.post(reverse('reader:book_view'), {'book_id': book.id, 'chapter_id': chapters[0].id, 'kwd': 'English'})
        self.assertContains(search, 'English')
        # 搜索结果与 TXT 阅读模式共用同一套列表结构与样式
        self.assertContains(search, 'list-group-item')
        self.assertContains(search, 'search-chapter-label')
        self.assertContains(search, 'search-snippet')
        self.assertFalse(self.client.post(reverse('reader:book_rechapter', args=[book.id])).json()['success'])
        cache = Path(book.content_path) if Path(book.content_path).is_absolute() else Path.cwd() / book.content_path
        self.assertTrue(cache.exists())
        self.assertTrue(self.client.post(reverse('reader:book_local_del', args=[book.id])).json()['success'])
        self.assertFalse(cache.exists())
        self.assertFalse((self.folder / 'sample.epub').exists())

    def test_epub2_and_unsafe_archive(self):
        path = self.folder / 'test.epub'
        path.write_bytes(sample_epub(version=2))
        self.assertEqual(len(parse_epub(path)['chapters']), 2)
        path.unlink()
        response = self.client.post(reverse('reader:upload_file'), {'file': SimpleUploadedFile('bad.epub', sample_epub(unsafe=True))})
        self.assertFalse(response.json()['success'])
        self.assertFalse(Book.objects.exists())
        self.assertEqual(list(self.folder.iterdir()), [])
        # Size limits and entity expansion are checked before content is imported.
        path.write_bytes(sample_epub())
        with patch('reader.services.epub.MAX_ENTRIES', 1), self.assertRaises(EpubError):
            parse_epub(path)
        data = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(sample_epub())) as source, zipfile.ZipFile(data, 'w') as target:
            for name in source.namelist():
                value = source.read(name)
                if name == 'META-INF/container.xml':
                    value = b'<!DOCTYPE x [<!ENTITY attack SYSTEM "file:///etc/passwd">]>' + value
                target.writestr(name, value)
        path.write_bytes(data.getvalue())
        with self.assertRaises(EpubError):
            parse_epub(path)
        path.unlink()
        with patch('reader.services.book_parser.Chapter.objects.bulk_create', side_effect=RuntimeError('test failure')):
            response = self.client.post(reverse('reader:upload_file'), {'file': SimpleUploadedFile('failed.epub', sample_epub())})
        self.assertFalse(response.json()['success'])
        self.assertFalse(Book.objects.exists())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_kosync_epub_save_and_restore(self):
        book = self.upload()
        setting = get_or_create_user_setting(self.user)
        setting.progress_sync_mode = 'kosync'
        setting.save(update_fields=['progress_sync_mode'])
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': True})
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        response = self.client.post(reverse('reader:book_view'), {
            'book_id': book.id, 'chapter_id': chapters[0].id, 'words': 3,
        })
        self.assertTrue(response.json()['success'])
        record = KosyncProgress.objects.get(user=self.user)
        self.assertTrue(record.progress.startswith('/body/DocFragment['))

        UserBookRecord.objects.filter(user_id=self.user.id, book_id=book.id).delete()
        progress_path = self.folder / (book.md5 + '.json')
        if progress_path.exists():
            progress_path.unlink()
        remote = epub_to_koreader_progress(book, chapters[1], 4)
        record.progress = remote['progress']
        record.percentage = remote['percentage']
        record.save(update_fields=['progress', 'percentage', 'updated_at'])

        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertEqual(response.context['chapter_id'], chapters[1].id)
        self.assertEqual(response.context['last_words'], 4)

    def test_epub_save_falls_back_to_s3_when_kosync_disabled(self):
        book = self.upload()
        setting = get_or_create_user_setting(self.user)
        setting.progress_sync_mode = 'kosync'
        setting.save(update_fields=['progress_sync_mode'])
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': False})
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        with patch('reader.views.reader.sync_progress_to_s3') as sync_s3, \
                patch('reader.views.reader.kosync.update_book_progress') as update_kosync:
            response = self.client.post(reverse('reader:book_view'), {
                'book_id': book.id, 'chapter_id': chapters[0].id, 'words': 3,
            })
        self.assertTrue(response.json()['success'])
        sync_s3.assert_called_once()
        update_kosync.assert_not_called()
        self.assertFalse(KosyncProgress.objects.filter(user=self.user).exists())

    def test_epub_restore_falls_back_to_s3_when_kosync_disabled(self):
        book = self.upload()
        book.local_only = False
        book.save(update_fields=['local_only'])
        setting = get_or_create_user_setting(self.user)
        setting.progress_sync_mode = 'kosync'
        setting.save(update_fields=['progress_sync_mode'])
        SystemSetting.objects.update_or_create(pk=1, defaults={'kosync_enabled': False})
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        UserBookRecord.objects.filter(user_id=self.user.id, book_id=book.id).delete()
        progress_path = self.folder / (book.md5 + '.json')
        if progress_path.exists():
            progress_path.unlink()
        remote = {'schemaVersion': 1, 'bookId': book.md5,
                  **export_position(book, chapters[1], 4),
                  'lastReadTime': '2099-01-01T00:00:00Z'}
        cloud = Mock()
        cloud.get_object.return_value = {'Body': io.BytesIO(json.dumps(remote).encode())}
        cfg = {'bucket': 'test', 'prefix': 'test/'}
        with patch('reader.services.epub_progress.get_s3_config', return_value=cfg), \
                patch('reader.services.epub_progress._get_s3_client', return_value=cloud), \
                patch('reader.services.epub_progress._kosync_record') as kosync_record:
            response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        kosync_record.assert_not_called()
        cloud.get_object.assert_called_once()
        self.assertEqual(response.context['chapter_id'], chapters[1].id)
        self.assertEqual(response.context['last_words'], 4)

    def test_kosync_epub_position_roundtrip(self):
        from .services.kosync_position import epub_to_koreader_progress, koreader_progress_to_epub
        book = self.upload()
        chapter = Chapter.objects.filter(book_id=book.id).order_by('index').first()
        for words in (0, 3, 5):
            position = epub_to_koreader_progress(book, chapter, words)
            self.assertIn('/DocFragment[', position['progress'])
            restored, restored_words, exact, _position = koreader_progress_to_epub(
                book, position['progress'], position['percentage'])
            self.assertTrue(exact)
            self.assertEqual(restored.id, chapter.id)
            self.assertEqual(restored_words, words)

    def test_plain_restore_keeps_book_id_after_post(self):
        book = self.upload()
        chapter = Chapter.objects.filter(book_id=book.id).order_by('index').first()
        response = self.client.post(reverse('reader:book_view'), {
            'book_id': book.id, 'chapter_id': chapter.id,
        })
        self.assertContains(response, "url.searchParams.set('book_id', '%d')" % book.id)

    def test_epub3_nested_toc_volumes(self):
        path = self.folder / 'nested3.epub'
        path.write_bytes(sample_epub(nested=True))
        data = parse_epub(path)
        self.assertEqual([n['title'] for n in data['tocTree']], ['第一卷', '第二卷'])
        self.assertEqual([len(n['children']) for n in data['tocTree']], [1, 1])
        self.assertTrue(all(n.get('start') is not None for n in data['tocTree']))

        book = self.upload(sample_epub(nested=True), 'nested3.epub')
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertEqual([c.title for c in chapters], ['第一章', '第二章'])
        self.assertEqual([(c.volume_index, c.volume_title) for c in chapters],
                         [(0, '第一卷'), (1, '第二卷')])
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertEqual(response.context['epub_config']['tocTree'][0]['title'], '第一卷')
        listing = self.client.get(reverse('reader:chapter_list', args=[book.id])).json()
        self.assertIn('volume-group', listing['html'])
        self.assertIn('第一卷', listing['html'])

    def test_epub2_nested_ncx_volumes(self):
        path = self.folder / 'nested2.epub'
        path.write_bytes(sample_epub(version=2, nested=True))
        data = parse_epub(path)
        self.assertEqual([n['title'] for n in data['tocTree']], ['第一卷', '第二卷'])
        self.assertEqual([len(n['children']) for n in data['tocTree']], [1, 1])

        book = self.upload(sample_epub(version=2, nested=True), 'nested2.epub')
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertEqual([c.title for c in chapters], ['第一章', '第二章'])
        self.assertEqual([(c.volume_index, c.volume_title) for c in chapters],
                         [(0, '第一卷'), (1, '第二卷')])

    def test_flat_toc_keeps_flat_chapters(self):
        book = self.upload()
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertTrue(all(c.volume_index is None and c.volume_title == '' for c in chapters))
        tree = read_cache(book)['tocTree']
        self.assertEqual([n['title'] for n in tree], ['第一章', '第二章'])
        self.assertTrue(all(not n['children'] for n in tree))
        listing = self.client.get(reverse('reader:chapter_list', args=[book.id])).json()
        self.assertNotIn('volume-group', listing['html'])

    def test_old_epub_cache_backfills_volumes(self):
        book = self.upload(sample_epub(nested=True), 'old-nested.epub')
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        UserBookRecord.objects.create(user_id=self.user.id, book_id=book.id,
                                      chapter_id=chapters[1].id, words_read=2, progress=50)
        cache_path = Path(book.content_path) if Path(book.content_path).is_absolute() else Path.cwd() / book.content_path
        cached = json.loads(cache_path.read_text())
        cached.pop('tocTree', None)
        cache_path.write_text(json.dumps(cached))
        Chapter.objects.filter(book_id=book.id).update(volume_index=None, volume_title='')
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertEqual(response.context['epub_config']['tocTree'][0]['title'], '第一卷')
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertEqual([(c.volume_index, c.volume_title) for c in chapters],
                         [(0, '第一卷'), (1, '第二卷')])
        self.assertEqual(response.context['chapter_id'], chapters[1].id)
        self.assertEqual(response.context['last_words'], 2)
        self.assertIn('tocTree', json.loads(cache_path.read_text()))

    def test_epub_import_marks_volume_version(self):
        book = self.upload()
        self.assertEqual(read_cache(book).get('volumeVersion'), EPUB_VOLUME_VERSION)

    def test_legacy_wrong_volumes_are_recalculated_once(self):
        book = self.upload()
        cache_path = Path(book.content_path) if Path(book.content_path).is_absolute() else Path.cwd() / book.content_path
        cached = json.loads(cache_path.read_text())
        cached.pop('volumeVersion', None)
        cache_path.write_text(json.dumps(cached))
        # Simulate the old algorithm treating ordinary chapters as volumes.
        Chapter.objects.filter(book_id=book.id).update(volume_index=0, volume_title='第一章')

        ensure_epub_volumes(book)
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertTrue(all(c.volume_index is None and c.volume_title == '' for c in chapters))
        self.assertEqual(json.loads(cache_path.read_text())['volumeVersion'], EPUB_VOLUME_VERSION)

        # Once the marker is current, opening the book must not redo recognition.
        with patch('reader.services.book_parser._assign_epub_volumes') as assign:
            ensure_epub_volumes(book)
            assign.assert_not_called()

    def test_positions_roundtrip_and_chinese_element_units(self):
        book = self.upload()
        chapter = Chapter.objects.filter(book_id=book.id).order_by('index').first()
        for words in (0, 3, 5, 16):
            position = export_position(book, chapter, words)
            restored, offset, exact = import_position(book, position)
            self.assertEqual((restored.id, offset, exact), (chapter.id, words, True))
        restored, words, exact = import_position(book, {'sectionIndex': 1, 'paragraphIndex': 1, 'elementIndex': 2})
        self.assertEqual((restored.id, words, exact), (chapter.id, 4, True))
        self.assertFalse(import_position(book, {'sectionIndex': 999, 'readProgress': 5000})[2])

    def test_remote_restore_does_not_upload_and_preserves_extensions(self):
        book = self.upload()
        book.local_only = False
        book.save()
        remote = {'schemaVersion': 1, 'bookId': book.md5, 'sectionIndex': 1,
                  'paragraphIndex': 2, 'elementIndex': 0, 'readProgress': 7500,
                  'lastReadTime': '2026-10-18T01:00:00Z', 'custom': {'keep': True},
                  'todayStats': {'date': '2026-10-18', 'devices': {}}}
        cloud = Mock()
        cloud.get_object.side_effect = lambda **kwargs: {'Body': io.BytesIO(json.dumps(remote).encode()), 'ETag': 'sample-etag'}
        cfg = {'bucket': 'test', 'prefix': 'test/'}
        with patch('reader.services.epub_progress.get_s3_config', return_value=cfg), patch('reader.services.epub_progress._get_s3_client', return_value=cloud):
            response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        second = Chapter.objects.filter(book_id=book.id, index=1).get()
        self.assertEqual(response.context['chapter_id'], second.id)
        cloud.put_object.assert_not_called()

        with patch('reader.services.progress.get_device_id', return_value='test-device'):
            save_progress_json(book, second, 2, self.user.id)
        saved = json.loads((self.folder / (book.md5 + '.json')).read_text())
        self.assertEqual(saved['custom'], remote['custom'])
        with patch('reader.services.s3.get_s3_config', return_value=cfg), patch('reader.services.s3._get_s3_client', return_value=cloud):
            sync_progress_to_s3(Mock(user=self.user), book)
        published = json.loads(cloud.put_object.call_args.kwargs['Body'])
        self.assertEqual(published['custom'], remote['custom'])
        self.assertEqual(cloud.put_object.call_args.kwargs['IfMatch'], 'sample-etag')
        cloud.reset_mock()
        remote['lastReadTime'] = '2099-01-01T00:00:00Z'
        with patch('reader.services.s3.get_s3_config', return_value=cfg), patch('reader.services.s3._get_s3_client', return_value=cloud):
            sync_progress_to_s3(Mock(user=self.user), book)
        cloud.put_object.assert_not_called()
        cloud.get_object.side_effect = ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')
        with patch('reader.services.s3.get_s3_config', return_value=cfg), patch('reader.services.s3._get_s3_client', return_value=cloud):
            sync_progress_to_s3(Mock(user=self.user), book)
        cloud.put_object.assert_not_called()

    def test_approximate_restore_never_rewrites_progress(self):
        book = self.upload()
        book.local_only = False
        book.save()
        record = {'schemaVersion': 1, 'bookId': book.md5, 'sectionIndex': 999,
                  'paragraphIndex': 0, 'elementIndex': 0, 'readProgress': 7500,
                  'lastReadTime': '2026-10-18T01:00:00Z'}
        path = self.folder / (book.md5 + '.json')
        path.write_text(json.dumps(record))
        with patch('reader.services.epub_progress.get_s3_config', return_value=None):
            response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertTrue(response.context['progress_approximate'])
        self.assertEqual(json.loads(path.read_text()), record)

    def test_remote_library_and_book_list_import(self):
        cfg = {'bucket': 'test', 'prefix': 'test/'}
        cloud = Mock()
        cloud.list_objects_v2.return_value = {'Contents': [{'Key': 'test/books/remote.epub', 'Size': 123}]}
        cloud.download_file.side_effect = lambda bucket, key, path: Path(path).write_bytes(sample_epub())
        with patch('reader.views.book_list.get_s3_config', return_value=cfg), patch('reader.views.book_list._get_s3_client', return_value=cloud), patch('reader.views.book_list.get_local_books_dir', return_value=str(self.folder)):
            response = self.client.get(reverse('reader:book_list_remote_books'))
            self.assertEqual(response.json()['books'][0]['name'], 'remote.epub')
            book_list = BookList.objects.create(name='EPUB', user_id=self.user.id)
            item = BookListItem.objects.create(book_list_id=book_list.id, manual_name='remote', remote_file_name='remote.epub')
            response = self.client.post(reverse('reader:book_list_download_remote', args=[book_list.id, item.id]))
            self.assertTrue(response.json()['success'])
            item.refresh_from_db()
            self.assertGreater(item.book_id, 0)
        with patch('reader.views.books.get_s3_config', return_value=cfg), patch('reader.views.books._get_s3_client', return_value=cloud):
            response = self.client.post(reverse('reader:open_remote_book'), {'name': 'other.epub'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(Book.objects.get(file_name='other.epub').format, 'epub')
        cloud.put_object.assert_not_called()

    def test_txt_still_imports_and_reads(self):
        book = self.upload('前言\n第1章 开始\n正文内容\n'.encode(), 'old.txt')
        self.assertEqual(book.format, 'txt')
        self.assertIn('正文内容', read_book_text(book))
        self.assertEqual(self.client.get(reverse('reader:book_view'), {'book_id': book.id}).status_code, 200)

    def test_presentations_share_shell_and_switch_preserves_position(self):
        book = self.upload()
        chapter = Chapter.objects.filter(book_id=book.id).order_by('index').first()
        for display in ('rich', 'plain'):
            response = self.client.get(reverse('reader:book_view'), {
                'book_id': book.id, 'display': display, 'chapter_id': chapter.id, 'offset': 7,
            })
            self.assertEqual(response.context['last_words'], 7)
            self.assertContains(response, 'id="epub-display"')
            self.assertContains(response, '>原书排版</option>')
            self.assertNotContains(response, 'epub-add-bookmark')
            self.assertNotContains(response, '添加当前书签')
            if display == 'rich':
                self.assertContains(response, 'aria-label="排版样式"')
            self.assertContains(response, 'id="offcanvassetting"')
            self.assertContains(response, 'class="pages-container join')
            self.assertContains(response, 'chapter_list_container')
            self.assertNotContains(response, 'class="epub-toolbar"')
            self.assertEqual(response.context['epub_switch_config']['display'], display)
        self.assertFalse(UserBookRecord.objects.filter(book_id=book.id).exists())

    def test_rich_resources_css_and_permissions(self):
        data = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(sample_epub())) as source, zipfile.ZipFile(data, 'w') as archive:
            for name in source.namelist():
                raw = source.read(name)
                if name.endswith('book.opf'):
                    raw = raw.replace(b'</manifest>', b'<item id="css" href="style.css" media-type="text/css"/><item id="pic" href="pic.png" media-type="image/png"/></manifest>')
                if name.endswith('body.xhtml'):
                    raw = b'''<html><head><link rel="stylesheet" href="style.css"></head><body><p>English don't \xe2\x80\x98change\xe2\x80\x99.</p><img src="pic.png" onload="attack()"><table><tr><th colspan="2">Title</th></tr><tr><td rowspan="2">A</td><td>B</td></tr></table><a href="#note" epub:type="noteref">1</a><aside id="note">Note</aside><script>attack()</script><iframe src="/admin/"></iframe><img src="https://bad.test/track"></body></html>'''
                archive.writestr(name, raw)
            archive.writestr('OEBPS/style.css', 'p{color:red;background:url("https://bad.test/x")} @import "https://bad.test/a";td{padding:4px}body{background:url(pic.png)}')
            archive.writestr('OEBPS/pic.png', b'picture')
        book = self.upload(data.getvalue())
        url = reverse('reader:epub_resource', args=[book.id])
        response = self.client.get(url, {'path': 'OEBPS/body.xhtml'})
        self.assertContains(response, 'colspan="2"')
        self.assertContains(response, 'rowspan="2"')
        self.assertContains(response, 'epub:type="noteref"')
        self.assertContains(response, 'OEBPS%2Fbody.xhtml#note')
        self.assertNotContains(response, 'attack()')
        self.assertNotContains(response, 'bad.test')
        self.assertNotContains(response, '<iframe')
        self.assertContains(response, 'English don\'t ‘change’.')
        self.assertIn("script-src 'none'", response['Content-Security-Policy'])
        self.assertNotIn('sessionid', response.cookies)
        self.assertIn('Cookie', response['Vary'])
        css = self.client.get(url, {'path': 'OEBPS/style.css'})
        self.assertContains(css, 'color:red')
        self.assertContains(css, 'OEBPS%2Fpic.png')
        self.assertNotContains(css, 'bad.test')
        self.assertEqual(self.client.get(url, {'path': '../secret'}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url, {'path': 'OEBPS/pic.png'}).status_code, 404)
        book.share = True
        book.save()
        self.assertEqual(self.client.get(url, {'path': 'OEBPS/pic.png'}).content, b'picture')

    def test_fixed_layout_image_only_progress(self):
        data = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(sample_epub())) as source, zipfile.ZipFile(data, 'w') as archive:
            for name in source.namelist():
                raw = source.read(name)
                if name.endswith('book.opf'):
                    raw = raw.replace(b'</metadata>', b'<meta property="rendition:layout">pre-paginated</meta></metadata>')
                if name.endswith('.xhtml') and not name.endswith('nav.xhtml'):
                    raw = b'<html><head><meta name="viewport" content="width=800,height=1200"></head><body><img src="pic.png"></body></html>'
                archive.writestr(name, raw)
        book = self.upload(data.getvalue())
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertEqual(len(chapters), 2)
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id, 'chapter_id': chapters[1].id})
        self.assertEqual(response.context['epub_config']['section'], 1)
        plain = self.client.get(reverse('reader:book_view'), {'book_id': book.id, 'display': 'plain'})
        self.assertTrue(plain.context['epub_rich'])
        self.assertFalse(plain.context['epub_has_text'])
        response = self.client.post(reverse('reader:bookmark_save'), {
            'book_id': book.id, 'chapter_id': chapters[1].id, 'words_read': 0,
            'epub_location': json.dumps({'section': 1, 'fraction': 0}),
        })
        self.assertEqual(response.content, b'ok')
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertEqual(response.context['epub_config']['bookmarks'][0]['epub_location']['section'], 1)
        self.assertTrue(response.context['epub_config']['sections'][1]['fixed'])
        self.assertEqual(response.context['epub_config']['sections'][1]['paragraphs'], [])
        response = self.client.post(reverse('reader:book_view'), {
            'book_id': book.id, 'chapter_id': chapters[1].id, 'words': 0,
            'epub_location': json.dumps({'section': 1, 'fraction': 0}),
        })
        self.assertTrue(response.json()['success'])
        response = self.client.get(reverse('reader:book_view'), {'book_id': book.id})
        self.assertEqual(response.context['epub_config']['section'], 1)
