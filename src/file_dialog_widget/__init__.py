"""file_dialog_widget

PyQt5 / PyQt6 / PySide2 / PySide6 (qtpy) 모두에서 동작하는,
QFileDialog 기반 경로 선택 위젯.

    from file_dialog_widget import FilePathEdit

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
from .history import configure_settings
from .hooks import GuardedFileSystemModel, guard_dialog, install_hooks
from .icons import CategoryIconProvider, clock_icon, star_icon
from .menus import FavoritesMenus
from .path_edit import FilePathEdit
from .places import Places, current_sidebar_urls
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
    "CategoryIconProvider",
    # 다이얼로그 직접 다루기
    "exec_file_dialog",
    "resolve_start_dir",
    "current_sidebar_urls",
    "to_urls",
    "install_hooks",
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
    "configure_settings",
    "safety",
    "__version__",
]
