import os
import re
import tempfile
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import Book, Chapter, UserSetting
from .services import volume
from .services.book_parser import (
    _resolve_volume_rule, _split_into_chapters, handle_local_book, rechapter_book,
)


def make_chapter(index, volume_index=None, volume_title=''):
    return SimpleNamespace(
        id=index + 1, book_id=1, index=index,
        volume_index=volume_index, volume_title=volume_title,
    )


class VolumeMapTests(TestCase):
    def setUp(self):
        self.chapters = [
            make_chapter(0, 0, '第一卷 风起'),
            make_chapter(1, 0, '第一卷 风起'),
            make_chapter(2, 0, '第一卷 风起'),
            make_chapter(3, 1, '第二卷 云涌'),
            make_chapter(4, 1, '第二卷 云涌'),
            make_chapter(5, 2, '第三卷 惊雷'),
        ]

    def test_group_chapters(self):
        groups = volume.group_chapters(self.chapters)
        self.assertEqual([g['title'] for g in groups], ['第一卷 风起', '第二卷 云涌', '第三卷 惊雷'])
        self.assertEqual([len(g['chapters']) for g in groups], [3, 2, 1])

    def test_group_chapters_keeps_unvolumed_chapters(self):
        chapters = [make_chapter(0), make_chapter(1, 0, '第一卷'), make_chapter(2)]
        groups = volume.group_chapters(chapters)
        self.assertEqual([g['title'] for g in groups], ['', '第一卷', ''])
        self.assertEqual([len(g['chapters']) for g in groups], [1, 1, 1])

    def test_sync_index_forward(self):
        self.assertTrue(volume.has_volumes(self.chapters))
        self.assertEqual(volume.sync_index_for_chapter(self.chapters, 0), 1)
        self.assertEqual(volume.sync_index_for_chapter(self.chapters, 1), 2)
        self.assertEqual(volume.sync_index_for_chapter(self.chapters, 3), 5)
        self.assertEqual(volume.sync_index_for_chapter(self.chapters, 5), 8)

    def test_sync_index_reverse(self):
        self.assertEqual(volume.chapter_index_for_sync(self.chapters, 0), 0)
        self.assertEqual(volume.chapter_index_for_sync(self.chapters, 2), 1)
        self.assertEqual(volume.chapter_index_for_sync(self.chapters, 4), 3)
        self.assertEqual(volume.chapter_index_for_sync(self.chapters, 5), 3)
        self.assertEqual(volume.chapter_index_for_sync(self.chapters, 8), 5)
        self.assertIsNone(volume.chapter_index_for_sync(self.chapters, 99))

    def test_progress_round_trip(self):
        data = {'sectionIndex': 3, 'paragraphIndex': 7, 'elementIndex': 3, 'readProgress': 1200}
        synced = volume.progress_to_sync(data, self.chapters)
        self.assertEqual(synced['sectionIndex'], 5)
        self.assertEqual(synced[volume.VOLUME_SYNC_KEY], volume.VOLUME_SYNC_VERSION)
        restored = volume.progress_from_sync(synced, self.chapters)
        self.assertEqual(restored['sectionIndex'], 3)
        self.assertEqual(restored['paragraphIndex'], 7)
        self.assertNotIn(volume.VOLUME_SYNC_KEY, restored)

    def test_progress_from_volume_entry(self):
        synced = {
            'sectionIndex': 4, 'paragraphIndex': 9, 'elementIndex': 5,
            volume.VOLUME_SYNC_KEY: volume.VOLUME_SYNC_VERSION,
        }
        restored = volume.progress_from_sync(synced, self.chapters)
        self.assertEqual(restored['sectionIndex'], 3)
        self.assertEqual(restored['paragraphIndex'], 0)
        self.assertEqual(restored['elementIndex'], 0)

    def test_legacy_progress_without_marker_is_unchanged(self):
        data = {'sectionIndex': 3, 'paragraphIndex': 2}
        self.assertEqual(volume.progress_from_sync(data, self.chapters), data)

    def test_no_volumes_uses_original_index(self):
        chapters = [make_chapter(0), make_chapter(1)]
        data = {'sectionIndex': 1}
        self.assertEqual(volume.progress_to_sync(data, chapters), data)
        self.assertEqual(volume.sync_index_for_chapter(chapters, 1), 1)
        self.assertEqual(volume.chapter_index_for_sync(chapters, 1), 1)


class VolumeParserTests(TestCase):
    DATA = (
        '第一卷 风起\n'
        '第一章 初入江湖\n正文一\n'
        '第二章 初遇\n正文二\n'
        '第三章 离别\n正文三\n'
        '第二卷 云涌\n'
        '第一章 重逢\n正文四\n'
        '第二章 决战\n正文五\n'
    )
    CHAPTER_RULE = r'^第[一二三]章.*$'
    VOLUME_RULE = r'^第[一二]卷.*$'

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='volume-user')
        UserSetting.objects.create(
            user_id=self.user.id,
            chapter_rule=self.CHAPTER_RULE,
            chapter_rule_2=r'^第[一二三]章 初.*$',
            volume_rule=self.VOLUME_RULE,
            volume_rule_2=r'^第[一二]卷.*$',
            chapter_min_len=0,
            chapter_max_len=0,
        )
        self.book = Book.objects.create(
            book_url='local/test-volume.txt', name='test', author='',
            intro='', rule='', first_chapter_title='', last_chapter_title='',
            charset='utf-8',
        )

    def split(self, data=None, volume_rule=VOLUME_RULE):
        data = data if data is not None else self.DATA
        match = re.compile(self.CHAPTER_RULE, re.MULTILINE).finditer(data)
        return _split_into_chapters(
            self.book, data, match, '/tmp/test-volume.txt', set_md5=False,
            min_len=0, max_len=0, volume_rule=volume_rule,
        )

    def test_volume_titles_are_not_chapters(self):
        self.split()
        titles = list(Chapter.objects.filter(book_id=self.book.id).order_by('index')
                      .values_list('title', flat=True))
        self.assertIn('前言', titles)
        self.assertNotIn('第一卷 风起', titles)
        self.assertNotIn('第二卷 云涌', titles)
        self.assertEqual(titles.count('第一章 初入江湖'), 1)

    def test_chapters_get_volume_info(self):
        self.split()
        chapters = list(Chapter.objects.filter(book_id=self.book.id).order_by('index'))
        self.assertEqual([c.index for c in chapters], list(range(len(chapters))))
        self.assertTrue(all(c.volume_index == 0 for c in chapters[:4]))
        self.assertTrue(all(c.volume_title == '第一卷 风起' for c in chapters[:4]))
        self.assertTrue(all(c.volume_index == 1 for c in chapters[4:]))
        self.assertTrue(all(c.volume_title == '第二卷 云涌' for c in chapters[4:]))

    def test_empty_volume_rule_clears_volume_info(self):
        self.split()
        Chapter.objects.filter(book_id=self.book.id).delete()
        self.split(volume_rule='')
        chapters = list(Chapter.objects.filter(book_id=self.book.id).order_by('index'))
        self.assertTrue(all(c.volume_index is None and c.volume_title == '' for c in chapters))

    def test_volume_rule_pairs_with_chapter_rule(self):
        setting = UserSetting.objects.get(user_id=self.user.id)
        self.assertEqual(_resolve_volume_rule(self.user, 'main'), setting.volume_rule)
        self.assertEqual(_resolve_volume_rule(self.user, 'rule_2'), setting.volume_rule_2)
        # 备用规则为空时回退到主规则，分卷规则也回退到主分卷规则。
        setting.chapter_rule_2 = ''
        setting.save(update_fields=['chapter_rule_2'])
        self.assertEqual(_resolve_volume_rule(self.user, 'rule_2'), setting.volume_rule)

    def test_volume_lines_matching_chapter_rule_are_filtered(self):
        rule = r'^第[一二]卷.*$|^第[一二三]章.*$'
        match = re.compile(rule, re.MULTILINE).finditer(self.DATA)
        _split_into_chapters(
            self.book, self.DATA, match, '/tmp/test-volume.txt', set_md5=False,
            min_len=0, max_len=0, volume_rule=self.VOLUME_RULE,
        )
        chapters = list(Chapter.objects.filter(book_id=self.book.id).order_by('index'))
        titles = [c.title for c in chapters]
        self.assertNotIn('第一卷 风起', titles)
        self.assertNotIn('第二卷 云涌', titles)
        self.assertEqual(sum(1 for t in titles if t.startswith('第')), 5)
        self.assertTrue(all(c.volume_index is not None for c in chapters))


class NewBookImportVolumeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='import-user')
        UserSetting.objects.create(
            user_id=self.user.id,
            chapter_rule=VolumeParserTests.CHAPTER_RULE,
            volume_rule=VolumeParserTests.VOLUME_RULE,
            chapter_min_len=0,
            chapter_max_len=0,
        )
        fd, self.path = tempfile.mkstemp(suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(VolumeParserTests.DATA)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_import_applies_volume_rule(self):
        request = RequestFactory().post('/')
        request.user = self.user
        self.assertTrue(handle_local_book(request, self.path))
        book = Book.objects.latest('id')
        chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
        self.assertTrue(chapters)
        self.assertTrue(any(c.volume_index == 0 for c in chapters))
        self.assertTrue(any(c.volume_index == 1 for c in chapters))
        self.assertFalse(any(c.title == '第一卷 风起' for c in chapters))


class RechapterVolumeTests(TestCase):
    DATA = VolumeParserTests.DATA

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='rechapter-user')
        self.setting = UserSetting.objects.create(
            user_id=self.user.id,
            chapter_rule=VolumeParserTests.CHAPTER_RULE,
            volume_rule=VolumeParserTests.VOLUME_RULE,
            chapter_min_len=0,
            chapter_max_len=0,
        )
        fd, self.path = tempfile.mkstemp(suffix='.txt')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(self.DATA)
        self.book = Book.objects.create(
            book_url=self.path, name='rechapter', author='', intro='', rule='',
            first_chapter_title='', last_chapter_title='', charset='utf-8',
        )
        self.split(with_volume=True)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def split(self, with_volume=True):
        data = self.DATA
        match = re.compile(VolumeParserTests.CHAPTER_RULE, re.MULTILINE).finditer(data)
        _split_into_chapters(
            self.book, data, match, self.path, set_md5=False,
            min_len=0, max_len=0,
            volume_rule=VolumeParserTests.VOLUME_RULE if with_volume else '',
        )

    def test_rechapter_reapplies_volume_and_clears_when_empty(self):
        self.assertTrue(Chapter.objects.filter(book_id=self.book.id, volume_index=0).exists())

        self.setting.volume_rule = ''
        self.setting.save(update_fields=['volume_rule'])
        self.assertTrue(rechapter_book(self.book, self.user, rule_choice='main'))

        chapters = list(Chapter.objects.filter(book_id=self.book.id).order_by('index'))
        self.assertTrue(chapters)
        self.assertTrue(all(c.volume_index is None and c.volume_title == '' for c in chapters))


class VolumeChapterListTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='chapter-list-user', is_superuser=True, is_staff=True,
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            book_url='local/test-list.txt', name='list', author='', intro='',
            rule='', first_chapter_title='', last_chapter_title='', charset='utf-8',
        )

    def add_chapters(self, volumes):
        chapters = []
        for index, (title, vi, vt) in enumerate(volumes):
            chapters.append(Chapter.objects.create(
                book_id=self.book.id, book_url=self.book.book_url, index=index,
                title=title, start=index * 10, end=index * 10 + 10,
                volume_index=vi, volume_title=vt,
            ))
        return chapters

    def test_two_level_list(self):
        self.add_chapters([
            ('第一章', 0, '第一卷 风起'),
            ('第二章', 0, '第一卷 风起'),
            ('第一章', 1, '第二卷 云涌'),
        ])
        response = self.client.get(
            reverse('reader:chapter_list', args=[self.book.id]),
            {'chapter_id': Chapter.objects.get(index=1, book_id=self.book.id).id},
        )
        data = response.json()
        self.assertTrue(data['success'])
        html = data['html']
        self.assertIn('第一卷 风起', html)
        self.assertIn('第二卷 云涌', html)
        self.assertEqual(html.count('class="volume-group"'), 2)
        self.assertIn('data-volume-index="0"', html)
        self.assertIn('data-chapter-id="' + str(Chapter.objects.get(index=0, book_id=self.book.id).id) + '"', html)
        self.assertEqual(len(data['chapter_ids']), 3)

    def test_flat_list_without_volumes(self):
        self.add_chapters([('第一章', None, ''), ('第二章', None, '')])
        response = self.client.get(reverse('reader:chapter_list', args=[self.book.id]))
        html = response.json()['html']
        self.assertNotIn('volume-group', html)
        self.assertEqual(html.count('list-group-item'), 2)


class VolumeSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='settings-user', is_superuser=True, is_staff=True,
        )
        self.client.force_login(self.user)
        self.url = reverse('reader:user_settings_rule')

    def test_invalid_volume_rule_rejected(self):
        response = self.client.post(self.url, {
            'chapter_rule': r'^第.+章$',
            'volume_rule_2': r'([',
        })
        self.assertFalse(response.json()['success'])
        self.assertIn('正则表达式错误', response.json()['error'])

    def test_save_all_volume_rules(self):
        response = self.client.post(self.url, {
            'chapter_rule': r'^第.+章$',
            'chapter_rule_2': r'^Chapter .+$',
            'chapter_rule_3': r'^第.+节$',
            'volume_rule': r'^第.+卷$',
            'volume_rule_2': r'^Part .+$',
            'volume_rule_3': r'^第.+部$',
            'chapter_min_len': '100',
            'chapter_max_len': '100000',
        })
        self.assertTrue(response.json()['success'])
        setting = UserSetting.objects.get(user_id=self.user.id)
        self.assertEqual(setting.volume_rule, r'^第.+卷$')
        self.assertEqual(setting.volume_rule_2, r'^Part .+$')
        self.assertEqual(setting.volume_rule_3, r'^第.+部$')
