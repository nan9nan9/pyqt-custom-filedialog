"""custom_file_dialog

PyQt5 / PyQt6 / PySide2 / PySide6 (qtpy) 모두에서 동작하는,
QFileDialog 를 확장한 파일 다이얼로그.

쓰는 방법은 세 가지다.

1. :class:`CustomFileDialog` — QFileDialog 를 쓰던 그대로::

       dlg = CustomFileDialog(self, mode="open_file", filters=[("CSV", ["csv"])])
       if dlg.exec():
           paths = dlg.selectedFiles()

2. :func:`exec_file_dialog` — 한 줄로::

       paths, chosen = exec_file_dialog(self, "open_file", filters=[("CSV", ["csv"])])

3. :class:`FilePathEdit` — 화면에 붙이는 경로 입력 줄::

       edit = FilePathEdit(mode="open_file", label="입력 파일:")
       edit.path()
"""

from . import safety
from .constants import SelectMode
from .dialog import CustomFileDialog, exec_file_dialog, resolve_start_dir
from .favorites import (
    FavoritesError,
    FavoritesStore,
    configure_favorites,
    configured_base_dir,
    default_base_dir,
)
from .filters import build_filter, ensure_suffix, suffix_of
from .form import FilePathForm
from .guard import GuardedFileSystemModel, guard_dialog
from .history import configure_settings, last_dir, remember_dir
from .hooks import install_hooks
from .icons import CategoryIconProvider, clock_icon, home_icon, star_icon
from .links import (
    follow_link_directories,
    follow_link_on_parent,
    show_link_target_in_combo,
)
from .menus import FavoritesMenus
from .path_edit import FilePathEdit
from .places import CURRENT_LABEL, Places, current_sidebar_urls
from .recent import DEFAULT_RECENT_MAX, RecentStore, default_recent_dir
from .sidebar import fit_sidebar, mark_sidebar
from .util import to_urls
from .validators import validate_paths

__version__ = "0.1.0"

__all__ = [
    # 다이얼로그
    "CustomFileDialog",
    "exec_file_dialog",
    # 위젯
    "FilePathEdit",
    "FilePathForm",
    # 즐겨찾기 · 최근 파일
    "FavoritesStore",
    "FavoritesError",
    "RecentStore",
    "DEFAULT_RECENT_MAX",
    "configure_favorites",
    "configured_base_dir",
    "default_base_dir",
    "default_recent_dir",
    # 사이드바
    "Places",
    "current_sidebar_urls",
    "mark_sidebar",
    "fit_sidebar",
    "CURRENT_LABEL",
    "to_urls",
    # 아이콘
    "star_icon",
    "clock_icon",
    "home_icon",
    "CategoryIconProvider",
    # 우클릭 메뉴 · 링크 추적
    "FavoritesMenus",
    "install_hooks",
    "follow_link_directories",
    "follow_link_on_parent",
    "show_link_target_in_combo",
    # 안전
    "safety",
    "guard_dialog",
    "GuardedFileSystemModel",
    # 용도별 시작 위치
    "configure_settings",
    "last_dir",
    "remember_dir",
    "resolve_start_dir",
    # 헬퍼
    "SelectMode",
    "build_filter",
    "suffix_of",
    "ensure_suffix",
    "validate_paths",
    "__version__",
]
