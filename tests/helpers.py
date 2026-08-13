"""여러 테스트 파일이 함께 쓰는 도우미 — 픽스처가 아닌 평범한 함수들."""

import os
import shutil
import tempfile
import time

import pytest

from qtpy.QtCore import QDir, QMimeData, QPoint, QSettings, QUrl
from qtpy.QtGui import QDropEvent
from qtpy.QtWidgets import QApplication

from custom_file_dialog import (
    FavoritesError,
    FavoritesStore,
    FilePathEdit,
    FilePathForm,
    Places,
    RecentStore,
    SelectMode,
    build_filter,
    ensure_suffix,
    home_icon,
    suffix_of,
    to_urls,
    validate_paths,
)
from custom_file_dialog import dialog as dialog_module
from custom_file_dialog import history as history_module
from custom_file_dialog import hooks as hooks_module
from custom_file_dialog import places as places_module
from custom_file_dialog import qt_compat
from custom_file_dialog import recent as recent_module
from custom_file_dialog import sidebar as sidebar_module
from custom_file_dialog.history import PathHistory


def _guarded_dialog_in(qapp, directory):
    """차단 경로 안(``/user/myaccount``)에서 연 다이얼로그와 설치된 장치들."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import guard_dialog

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(directory)
    dialog.show()
    _spin(qapp, 500)
    return dialog, guard_dialog(dialog)


def _close_soon(dialog, accepted=True):
    """다음 이벤트 루프 차례에 다이얼로그를 닫는다.

    ``accept()`` 는 QFileDialog 가 자체 검증을 해서 조건이 안 맞으면 아무 일도
    하지 않는다(그러면 exec() 가 영원히 안 돌아온다). 검증을 건너뛰는
    ``done()`` 으로 닫아 테스트가 매달리지 않게 한다.
    """
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QDialog

    code = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    QTimer.singleShot(0, lambda: dialog.done(code))


def _run(dialog):
    """바인딩에 무관하게 exec 한다."""
    runner = getattr(dialog, "exec_", None) or dialog.exec
    return runner()


def _places_of(store):
    """저장소(하나 또는 여럿)를 Places 로 감싼다 — 즐겨찾기가 앞, 최근 파일이 뒤."""
    stores = list(store) if isinstance(store, (list, tuple)) else [store]
    kinds = {"recent" if isinstance(s, RecentStore) else "favorites": s for s in stores}
    return Places(**kinds)


def _make_tree(tmp_path):
    """서로 다른 폴더에 흩어진 파일 2개와 폴더 1개를 만든다."""
    a = tmp_path / "projA"
    b = tmp_path / "projB"
    a.mkdir()
    b.mkdir()
    design = a / "설계도.csv"
    design.write_text("x")
    report = b / "보고서.md"
    report.write_text("x")
    output = b / "산출물"
    output.mkdir()
    return str(design), str(report), str(output)


def _spin(app, ms=1500):
    """QFileSystemModel 이 비동기로 채워지므로 잠깐 이벤트를 돌린다."""
    import time

    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _assert_at_end(paths, expected):
    """저장소 항목(최근 파일 · 북마크)이 맨 뒤에 이 순서로 붙는지 검사."""
    assert paths[-len(expected):] == list(expected), paths
    # Computer("" 또는 "Computer")는 이제 넣지 않는다
    assert not [p for p in paths if p in ("", "Computer")], paths
    return len(paths) - len(expected)


def _touch(tmp_path, name):
    path = tmp_path / name
    path.write_text("x")
    return str(path)


def _menu_dialog(store, start_dir, extra_sidebar=(), confirm=False):
    """사이드바 메뉴가 걸린 QFileDialog 를 만들어 (dialog, menu) 로 돌려준다."""
    from qtpy.QtCore import QUrl
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import FavoritesMenus

    places = _places_of(store)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    sidebar = [QUrl.fromLocalFile(p) for p in extra_sidebar]
    for one in places.stores():
        sidebar += one.sidebar_urls()
    dialog.setSidebarUrls(sidebar)
    dialog.setDirectory(start_dir)
    menu = FavoritesMenus(dialog, places, confirm=confirm)
    assert menu.install()
    return dialog, menu


def _view_menu(menus, view, index):
    """파일 목록 우클릭 메뉴를 실제 코드로 구성해 돌려준다(모달 exec 는 안 한다)."""
    from qtpy.QtWidgets import QMenu

    return menus.path_at(view, index), (menus.build_view_menu(view, index) or QMenu())


def _menu_labels(menu):
    """구분선과 서브메뉴를 뺀 메뉴 항목 이름들."""
    return [
        action.text()
        for action in menu.actions()
        if not action.isSeparator() and action.menu() is None
    ]


def _submenu_of(menu):
    for action in menu.actions():
        if action.menu() is not None:
            return action.menu()
    return None


def _drop(widget, paths):
    """실제 드롭 이벤트를 만들어 위젯에 전달한다.

    Qt6 은 QPointF 만 받는다(QPoint 는 Qt5 의 암묵 변환에 기대던 것이라
    PyQt6 에서 TypeError). QPointF 는 세 바인딩 모두에서 통한다.
    """
    from qtpy.QtCore import QPointF, Qt

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)


def _dialog_start_dirs(monkeypatch):
    """exec_file_dialog 이 실제로 어느 폴더에서 열었는지 기록하는 스파이."""
    seen = []
    result = {"paths": []}

    def fake_run(parent, mode, caption, directory, *args, **kwargs):
        seen.append(directory)
        return list(result["paths"]), ""

    monkeypatch.setattr(dialog_module, "_run_dialog", fake_run)
    return seen, result

