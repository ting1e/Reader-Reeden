from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import BookList, BookListItem


class BookListReadStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.owner = user_model.objects.create_user(username='owner')
        cls.other = user_model.objects.create_user(username='other')
        cls.admin = user_model.objects.create_user(username='admin', is_superuser=True)
        cls.book_list = BookList.objects.create(
            name='测试书单', user_id=cls.owner.id, is_public=True,
        )
        cls.item = BookListItem.objects.create(
            book_list_id=cls.book_list.id, manual_name='三体', rating=4, review='原有短评',
        )

    def setUp(self):
        self.client.force_login(self.owner)
        self.update_url = reverse(
            'reader:book_list_update_item', args=[self.book_list.id, self.item.id],
        )

    def test_default_is_unread(self):
        self.assertEqual(self.item.read_status, 'unread')

    def test_owner_can_cycle_read_status(self):
        for status, label in [('read', '已读，点击标为弃读'),
                              ('abandoned', '弃读，点击标为未读'),
                              ('unread', '未读，点击标为已读')]:
            response = self.client.post(self.update_url, {'read_status': status})
            self.assertJSONEqual(response.content, {'success': True, 'read_status': status})
            self.item.refresh_from_db()
            self.assertEqual(self.item.read_status, status)
            self.assertEqual(self.item.rating, 4)
            self.assertEqual(self.item.review, '原有短评')
            detail = self.client.get(reverse('reader:book_list_detail', args=[self.book_list.id]))
            self.assertContains(detail, 'data-read-status="' + status + '"')
            self.assertContains(detail, 'aria-label="' + label + '"')
            if status == 'abandoned':
                self.assertContains(detail, 'is-abandoned')
                self.assertContains(detail, 'icon="bi:x-lg"')

    def test_other_user_and_anonymous_cannot_update(self):
        self.client.force_login(self.other)
        response = self.client.post(self.update_url, {'read_status': 'abandoned'})
        self.assertFalse(response.json()['success'])
        self.client.logout()
        self.assertEqual(self.client.post(self.update_url, {'read_status': 'abandoned'}).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.read_status, 'unread')

    def test_admin_can_update(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.update_url, {'read_status': 'abandoned'})
        self.assertJSONEqual(response.content, {'success': True, 'read_status': 'abandoned'})

    def test_invalid_state_is_rejected(self):
        response = self.client.post(self.update_url, {'read_status': 'invalid'})
        self.assertFalse(response.json()['success'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.read_status, 'unread')

    def test_visitor_sees_abandoned_status(self):
        BookListItem.objects.filter(id=self.item.id).update(read_status='abandoned')
        self.client.logout()
        response = self.client.get(reverse('reader:book_list_detail', args=[self.book_list.id]))
        self.assertContains(response, 'aria-label="弃读"')
        self.assertContains(response, 'icon="bi:x-lg"')
        self.assertNotContains(response, 'class="item-read-status item-read-toggle')
