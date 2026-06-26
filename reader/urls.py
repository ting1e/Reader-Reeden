from urllib import response
from django.urls import path

from . import views

app_name = 'reader'
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    # path('<int:pk>/', views.DetailView.as_view(), name='detail'),
    # path('<int:pk>/results/', views.ResultsView.as_view(), name='results'),
    path('books/', views.BookListView.as_view(), name='book_list'),
    path('books_remote/', views.BookListRemoteView.as_view(), name='book_list_remote'),
    path('open_remote/', views.open_remote_book, name='open_remote_book'),
    path('book_admin/', views.book_admin, name='book_admin'),
    path('book_local_del/<int:pk>/', views.book_local_del, name='book_local_del'),
    path('book_rechapter/<int:pk>/', views.book_rechapter, name='book_rechapter'),
    path('upload/', views.upload_file, name='upload_file'),

    path('login/', views.login_auth, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('update_setting/', views.update_setting, name='update_setting'),
    path('bookmark/', views.bookmark_save, name='bookmark_save'),
    path('bookmark_list/<int:user_id>/<int:book_id>/', views.BookmarkListView.as_view(), name='bookmark_list'),
    path('bookmark_admin/', views.bookmark_admin, name='bookmark_admin'),
    path('bookmark_del/<int:pk>/', views.bookmark_del, name='bookmark_del'),

    path('null', views.ret_null   , name='null'),
    path('test/', views.test_requset   , name='test'),


    path('view/', views.BookView, name='book_view'),
    path('chapter_content/<int:chapter_id>/', views.chapter_content, name='chapter_content'),
    path('chapter_list_ajax/<int:book_id>/', views.chapter_list_ajax, name='chapter_list_ajax'),

]