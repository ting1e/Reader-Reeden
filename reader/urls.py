from urllib import response
from django.urls import path

from . import views

app_name = 'reader'
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    # path('<int:pk>/', views.DetailView.as_view(), name='detail'),
    # path('<int:pk>/results/', views.ResultsView.as_view(), name='results'),
    path('upload_file/', views.upload_file, name='upload_file'),
    path('books/', views.BookListView.as_view(), name='book_list'),
    path('book_admin/', views.BookAdminView, name='book_admin'),
    path('chapter_list/<int:pk>/', views.ChapterListView.as_view(), name='chapter_list'),
    path('chapter_detail/<int:pk>/', views.ChapterDetailView.as_view(), name='chapter_detail'),

    path('book_temp/<int:pk>/', views.book, name='book'),
    # path('book/<int:book_pk>/<int:chapter_pk>/', views.book_reader, name='book_reader'),
    # path('book/<int:book_pk>/<int:chapter_pk>/<int:offset>/', views.book_reader_offset, name='book_reader_offset'),

    path('login/', views.login_auth, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('book_del/<int:pk>/', views.book_del, name='book_del'),
    path('update_setting/', views.update_setting, name='update_setting'),
    path('bookmark/', views.bookmark_save, name='bookmark_save'),
    path('bookmark_list/<int:user_id>/<int:book_id>/', views.BookmarkListView.as_view(), name='bookmark_list'),

    path('null', views.ret_null   , name='null'),
    path('test/', views.test_requset   , name='test'),


    path('view/', views.BookView, name='book_view'),
    path('chapter_content/<int:chapter_id>/', views.chapter_content, name='chapter_content'),

]