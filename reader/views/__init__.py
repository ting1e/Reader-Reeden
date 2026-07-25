# Re-export all views so urls.py can use `from . import views; views.XxxView`

from .auth import login_auth, logout_auth
from .setup import setup_admin
from .bookmark import (
    BookmarkListView, bookmark_admin, bookmark_del, bookmark_save,
    bookmark_list_legacy_redirect,
)
from .settings import (
    user_settings, user_settings_s3, user_settings_rule,
    user_settings_password, user_settings_update, user_settings_theme,
)
from .stats import reading_stats, reading_stats_admin, reading_stats_del
from .books import (
    BookshelfView, BookshelfRemoteView, IndexView,
    open_remote_book, book_admin, book_local_del, book_rechapter,
    book_share_toggle, book_rename, upload_file,
)
from .reader import book_view, chapter_content, chapter_list, keyword_search
from .fonts import font_admin, font_download, font_del, font_file
from .book_list import (
    book_list_index, book_list_admin, book_list_create, book_list_detail, book_list_edit,
    book_list_del, book_list_share_toggle, book_list_add_book,
    book_list_update_item, book_list_remove_item, book_list_remote_books,
    book_list_download_remote,
)
