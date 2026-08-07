"""custom_file_dialog

PyQt5 / PyQt6 / PySide2 / PySide6 (qtpy) 모두에서 동작하는,
QFileDialog 기반 경로 선택 위젯.

    from custom_file_dialog import FilePathEdit

    edit = FilePathEdit(mode="open_file", label="입력 파일:",
                        filters=[("CSV", ["csv"])])
"""

from . import safety
from .constants import SelectMode
from .dialog import exec_file_dialog, resolve_start_dir
from .favorites import (
    FavoritesError,
    FavoritesStore,
    configure_favorites,
    configured_base_dir,
    default_base_dir,
)
from .filters import build_filter, ensure_suffix, suffix_of
from .form import FilePathForm
from .history import configure_settings, last_dir, remember_dir
from .hooks import (
    GuardedFileSystemModel,
    follow_link_directories,
    follow_link_on_parent,
    guard_dialog,
    install_hooks,
    mark_sidebar,
    show_link_target_in_combo,
)
from .icons import CategoryIconProvider, clock_icon, home_icon, star_icon
from .menus import FavoritesMenus
from .path_edit import FilePathEdit
from .places import CURRENT_LABEL, Places, current_sidebar_urls
from .recent import DEFAULT_RECENT_MAX, RecentStore, default_recent_dir
from .util import to_urls
from .validators import validate_paths

__version__ = "0.1.0"

__all__ = [
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
    # 아이콘
    "star_icon",
    "clock_icon",
    "home_icon",
    "CategoryIconProvider",
    # 다이얼로그 직접 다루기
    "exec_file_dialog",
    "resolve_start_dir",
    "current_sidebar_urls",
    "to_urls",
    "install_hooks",
    "mark_sidebar",
    "CURRENT_LABEL",
    "follow_link_directories",
    "follow_link_on_parent",
    "show_link_target_in_combo",
    "guard_dialog",
    "Places",
    "GuardedFileSystemModel",
    "FavoritesMenus",
    # 헬퍼
    "SelectMode",
    "build_filter",
    "suffix_of",
    "ensure_suffix",
    "validate_paths",
    # 용도별 시작 위치
    "configure_settings",
    "last_dir",
    "remember_dir",
    "safety",
    "__version__",
]
