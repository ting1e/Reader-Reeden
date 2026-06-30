# Re-export all views so urls.py can use `from . import views; views.XxxView`

from .auth import login_auth, logout_view
from .setup import setup_admin
from .bookmark import BookmarkListView, bookmark_admin, bookmark_del, bookmark_save
from .settings import (
    user_settings, user_settings_s3, user_settings_rule,
    user_settings_password, update_setting, set_theme,
)
from .books import (
    BookListView, BookListRemoteView, IndexView,
    open_remote_book, book_admin, book_local_del, book_rechapter,
    book_share_toggle, upload_file,
)
from .reader import BookView, chapter_content, chapter_list_ajax, keyword_search
from .fonts import font_admin, font_download, font_del, font_file
