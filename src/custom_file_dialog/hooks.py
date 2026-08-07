"""다이얼로그에 거는 장치들을 **한 번에** 걸어 준다.

실제 구현은 관심사별로 나뉘어 있고, 여기서는 순서를 정해 함께 걸기만 한다.

===================================================  ==========================
:mod:`~custom_file_dialog.sidebar`                   사이드바 표시(홈 아이콘 ·
                                                     "현재 위치") 와 폭
:mod:`~custom_file_dialog.links`                     즐겨찾기 링크를 원본처럼
:mod:`~custom_file_dialog.guard`                     나열하면 안 되는 자리 방어
:mod:`~custom_file_dialog.menus`                     우클릭 메뉴
===================================================  ==========================

:class:`~custom_file_dialog.dialog.CustomFileDialog` 가 생성자에서 이 함수를
부르므로, 그 클래스를 쓰면 따로 걸 것이 없다. 직접 만든 ``QFileDialog`` 에
붙이고 싶을 때만 부르면 된다.

예전 이름들(``GuardedFileSystemModel`` · ``guard_dialog`` · ``mark_sidebar`` …)은
여기서 그대로 다시 내보내므로 ``from custom_file_dialog.hooks import ...`` 는
계속 동작한다.
"""

from .guard import GuardedFileSystemModel, guard_dialog
from .links import (
    follow_link_directories,
    follow_link_on_parent,
    show_link_target_in_combo,
)
from .menus import FavoritesMenus
from .sidebar import ENABLED_ROLE, fit_sidebar, mark_sidebar

__all__ = [
    "install_hooks",
    # 사이드바
    "mark_sidebar",
    "fit_sidebar",
    "ENABLED_ROLE",
    # 링크 추적
    "show_link_target_in_combo",
    "follow_link_directories",
    "follow_link_on_parent",
    # 차단 경로 방어
    "guard_dialog",
    "GuardedFileSystemModel",
]


def install_hooks(dialog, places, current=None):
    """다이얼로그에 사이드바 표시 · 링크 추적 · 메뉴 · 차단 경로 방어를 모두 건다.

    Args:
        dialog: 대상 ``QFileDialog`` (Qt 자체 다이얼로그여야 한다).
        places: :class:`~custom_file_dialog.places.Places`.
        current: 사이드바를 채울 때 쓴 "현재 위치". 주지 않으면 다이얼로그가
            지금 열려 있는 폴더를 쓴다.

    사이드바 표시와 차단 방어는 **얹을 것이 없어도** 건다(차단은 전역 설정이,
    홈 아이콘은 사이드바 자체가 있으면 의미가 있다). 링크 추적과 우클릭 메뉴는
    저장소가 있을 때만 건다.
    """
    guard_dialog(dialog)
    mark_sidebar(dialog, places, current)

    if not places.stores():
        return

    FavoritesMenus(dialog, places).install()
    follow_link_directories(dialog, places)      # 링크 폴더로 진입할 때
    show_link_target_in_combo(dialog, places)    # 목록에서 항목을 고를 때
    follow_link_on_parent(dialog, places)        # "상위 폴더" 로 올라갈 때
