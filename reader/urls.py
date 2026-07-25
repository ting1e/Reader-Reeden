from django.urls import path

from . import views

app_name = 'reader'
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('bookshelf/', views.BookshelfView.as_view(), name='bookshelf'),
    path('bookshelf_remote/', views.BookshelfRemoteView.as_view(), name='bookshelf_remote'),
    path('open_remote/', views.open_remote_book, name='open_remote_book'),
    path('book_admin/', views.book_admin, name='book_admin'),
    path('book_local_del/<int:pk>/', views.book_local_del, name='book_local_del'),
    path('book_rechapter/<int:pk>/', views.book_rechapter, name='book_rechapter'),
    path('book_share_toggle/<int:pk>/', views.book_share_toggle, name='book_share_toggle'),
    path('book_rename/<int:pk>/', views.book_rename, name='book_rename'),
    path('upload/', views.upload_file, name='upload_file'),

    path('login/', views.login_auth, name='login'),
    path('logout/', views.logout_auth, name='logout'),
    path('setup/', views.setup_admin, name='setup_admin'),
    path('update_setting/', views.user_settings_update, name='user_settings_update'),
    path('set_theme/', views.user_settings_theme, name='user_settings_theme'),
    path('bookmark/', views.bookmark_save, name='bookmark_save'),
    path('bookmark_list/<int:book_id>/', views.BookmarkListView.as_view(), name='bookmark_list'),
    # 旧 URL（含 user_id）保留做 301 重定向，去除 URL 中的冗余 user_id（IDOR 隐患设计）
    path('bookmark_list/<int:user_id>/<int:book_id>/', views.bookmark_list_legacy_redirect, name='bookmark_list_legacy'),
    path('bookmark_admin/', views.bookmark_admin, name='bookmark_admin'),
    path('bookmark_del/<int:pk>/', views.bookmark_del, name='bookmark_del'),
    path('user_settings/', views.user_settings, name='user_settings'),
    path('user_settings/s3/', views.user_settings_s3, name='user_settings_s3'),
    path('user_settings/rule/', views.user_settings_rule, name='user_settings_rule'),
    path('user_settings/password/', views.user_settings_password, name='user_settings_password'),
    path('reading_stats/', views.reading_stats, name='reading_stats'),
    path('reading_stats/admin/', views.reading_stats_admin, name='reading_stats_admin'),
    path('reading_stats/del/', views.reading_stats_del, name='reading_stats_del'),

    path('font_admin/', views.font_admin, name='font_admin'),
    path('font_download/', views.font_download, name='font_download'),
    path('font_del/<str:name>/', views.font_del, name='font_del'),
    path('font_file/<str:name>/', views.font_file, name='font_file'),

    path('view/', views.book_view, name='book_view'),
    path('chapter_content/<int:chapter_id>/', views.chapter_content, name='chapter_content'),
    path('chapter_list_ajax/<int:book_id>/', views.chapter_list, name='chapter_list'),

    path('booklist/', views.book_list_index, name='book_list_index'),
    path('booklist/admin/', views.book_list_admin, name='book_list_admin'),
    path('booklist/create/', views.book_list_create, name='book_list_create'),
    path('booklist/<int:pk>/', views.book_list_detail, name='book_list_detail'),
    path('booklist/<int:pk>/edit/', views.book_list_edit, name='book_list_edit'),
    path('booklist/<int:pk>/delete/', views.book_list_del, name='book_list_del'),
    path('booklist/<int:pk>/share_toggle/', views.book_list_share_toggle, name='book_list_share_toggle'),
    path('booklist/<int:pk>/add_book/', views.book_list_add_book, name='book_list_add_book'),
    path('booklist/<int:pk>/update_item/<int:item_id>/', views.book_list_update_item, name='book_list_update_item'),
    path('booklist/<int:pk>/remove_item/<int:item_id>/', views.book_list_remove_item, name='book_list_remove_item'),
    path('booklist/remote_books/', views.book_list_remote_books, name='book_list_remote_books'),
    path('booklist/<int:pk>/download_remote/<int:item_id>/', views.book_list_download_remote, name='book_list_download_remote'),

]