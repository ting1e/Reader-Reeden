from urllib import response
from django.urls import path

from . import views

app_name = 'reader'
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    # path('<int:pk>/', views.DetailView.as_view(), name='detail'),
    # path('<int:pk>/results/', views.ResultsView.as_view(), name='results'),
    path('books/', views.BookListView.as_view(), name='book_list'),

    path('login/', views.login_auth, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('update_setting/', views.update_setting, name='update_setting'),
    path('bookmark/', views.bookmark_save, name='bookmark_save'),
    path('bookmark_list/<int:user_id>/<int:book_id>/', views.BookmarkListView.as_view(), name='bookmark_list'),

    path('null', views.ret_null   , name='null'),
    path('test/', views.test_requset   , name='test'),


    path('view/', views.BookView, name='book_view'),
    path('chapter_content/<int:chapter_id>/', views.chapter_content, name='chapter_content'),
    path('chapter_list_ajax/<int:book_id>/', views.chapter_list_ajax, name='chapter_list_ajax'),

]