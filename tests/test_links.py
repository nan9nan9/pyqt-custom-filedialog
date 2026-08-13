"""심볼릭 링크 추적 — Look in 표시 · 진입 · 상위 폴더."""

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
from helpers import (
    _assert_at_end,
    _close_soon,
    _dialog_start_dirs,
    _drop,
    _guarded_dialog_in,
    _make_tree,
    _menu_dialog,
    _menu_labels,
    _places_of,
    _run,
    _spin,
    _submenu_of,
    _touch,
    _view_menu,
)


def test_link_target(qapp, tmp_path):
    """분류 안의 링크 폴더만 원본 위치로 매핑된다."""
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, output = _make_tree(tmp_path)
    favorites.add("설계", output)
    recent.record(design)
    places = Places(favorites=favorites, recent=recent)

    category = favorites.category_dir("설계")
    link = os.path.join(category, "산출물")

    # 링크 폴더 -> 원본
    assert places.link_target(link) == output
    # 링크 아래 하위 경로도 원본 기준으로 풀린다
    inner = os.path.join(link, "안쪽")
    os.mkdir(os.path.join(output, "안쪽"))
    assert places.link_target(inner) == os.path.join(output, "안쪽")

    # 분류 폴더 자체와 뿌리 폴더는 진짜 폴더이므로 그대로 둔다
    assert places.link_target(category) is None
    assert places.link_target(favorites.base_dir) is None
    assert places.link_target(recent.category_dir(recent.name)) is None

    # 저장소 밖은 손대지 않는다
    assert places.link_target(str(tmp_path)) is None
    assert places.link_target("") is None
    assert Places().link_target(link) is None


def test_follow_link_directories(qapp, tmp_path):
    """링크 폴더로 들어가면 Look in 에 실제 경로가 보이도록 옮겨 준다."""
    from qtpy.QtWidgets import QFileDialog

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    _design, _report, output = _make_tree(tmp_path)
    favorites.add("설계", output)
    category = favorites.category_dir("설계")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(str(tmp_path))
    assert hooks_module.follow_link_directories(dialog, Places(favorites=favorites))

    def go(path):
        dialog.setDirectory(path)
        dialog.directoryEntered.emit(path)       # 사용자 이동이면 Qt 가 내는 시그널
        return dialog.directory().absolutePath()

    # 링크 폴더 -> 원본 경로로 옮겨진다
    assert go(os.path.join(category, "산출물")) == output
    assert not favorites.is_inside(dialog.directory().absolutePath())

    # 분류 폴더 자체는 그대로 (진짜 폴더라 보여 줄 다른 경로가 없다)
    assert go(category) == category

    # 저장소 밖은 손대지 않는다
    plain = str(tmp_path / "projA")
    assert go(plain) == plain

    # 얹을 게 없는 Places 는 거짓이라 install_hooks 가 링크 추적을 건너뛴다
    assert not Places()


def test_follow_link_on_parent(qapp, tmp_path):
    """분류 폴더에서 링크를 고르고 "상위 폴더"를 누르면 원본 쪽으로 올라간다."""
    from qtpy.QtWidgets import QFileDialog, QToolButton

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, output = _make_tree(tmp_path)
    favorites.add("설계", design)
    favorites.add("설계", output)
    places = Places(favorites=favorites)
    category = favorites.category_dir("설계")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    assert hooks_module.follow_link_on_parent(dialog, places)
    button = dialog.findChild(QToolButton, "toParentButton")

    def press_up(selected=None):
        """항목을 고른 뒤 "상위 폴더"를 누른 상황을 그대로 재현한다."""
        dialog.setDirectory(category)
        if selected is not None:
            dialog.currentChanged.emit(selected)
        button.click()                      # Qt 가 분류 폴더의 부모로 옮긴 뒤...
        return dialog.directory().absolutePath()

    # 파일 링크 -> 원본 파일이 있는 폴더
    assert press_up(os.path.join(category, "설계도.csv")) == os.path.dirname(design)
    # 폴더 링크 -> 원본 폴더가 있는 폴더
    assert press_up(os.path.join(category, "산출물")) == os.path.dirname(output)

    # 아무것도 고르지 않았으면 Qt 기본 동작 그대로(저장소로 올라간다)
    assert press_up() == os.path.normpath(favorites.base_dir)
    # 링크가 아닌 것을 골랐을 때도 기본 동작
    assert press_up(os.path.join(category, "없는것")) == os.path.normpath(
        favorites.base_dir
    )


def test_follow_link_on_parent_outside_store(qapp, tmp_path):
    """저장소 밖에서는 손대지 않는다 — 묵은 선택이 새어 나가지 않는다."""
    from qtpy.QtWidgets import QFileDialog, QToolButton

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    places = Places(favorites=favorites)
    category = favorites.category_dir("설계")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    assert hooks_module.follow_link_on_parent(dialog, places)
    button = dialog.findChild(QToolButton, "toParentButton")

    # 링크를 고른 뒤 저장소 밖으로 옮기고 상위 폴더를 누른다
    dialog.currentChanged.emit(os.path.join(category, "설계도.csv"))
    inner = tmp_path / "projA" / "안쪽"
    inner.mkdir()
    dialog.setDirectory(str(inner))
    button.click()
    assert dialog.directory().absolutePath() == str(tmp_path / "projA")


def test_follow_link_on_parent_installed_by_hooks(qapp, tmp_path):
    """install_hooks 가 함께 걸어 준다."""
    from qtpy.QtWidgets import QFileDialog, QToolButton

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    places = Places(favorites=favorites)
    category = favorites.category_dir("설계")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    hooks_module.install_hooks(dialog, places, category)

    dialog.currentChanged.emit(os.path.join(category, "설계도.csv"))
    dialog.findChild(QToolButton, "toParentButton").click()
    assert dialog.directory().absolutePath() == os.path.dirname(design)


def test_show_link_target_in_combo(qapp, tmp_path):
    """항목을 고르면 콤보 표시만 실제 위치로 바뀌고, 폴더는 그대로 있는다."""
    from qtpy.QtWidgets import QComboBox, QFileDialog

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, output = _make_tree(tmp_path)
    favorites.add("설계", design)
    favorites.add("설계", output)
    recent.record(design)
    places = Places(favorites=favorites, recent=recent)

    category = favorites.category_dir("설계")
    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    assert hooks_module.show_link_target_in_combo(dialog, places)
    combo = dialog.findChild(QComboBox, "lookInCombo")

    # 파일 링크 -> 콤보에 원본 파일 경로
    dialog.currentChanged.emit(os.path.join(category, "설계도.csv"))
    assert combo.currentText() == design
    # 폴더는 그대로 분류에 머문다 (이동하지 않는다)
    assert dialog.directory().absolutePath() == category

    # 폴더 링크 -> 콤보에 원본 폴더 경로
    dialog.currentChanged.emit(os.path.join(category, "산출물"))
    assert combo.currentText() == output
    assert dialog.directory().absolutePath() == category

    # 링크가 아닌 항목을 고르면 현재 폴더 경로로 되돌아온다
    dialog.currentChanged.emit(os.path.join(category, "없는것"))
    assert combo.currentText() == category

    # 최근 파일 쪽도 동작한다
    recent_category = recent.category_dir(recent.name)
    dialog.setDirectory(recent_category)
    dialog.currentChanged.emit(os.path.join(recent_category, "설계도.csv"))
    assert combo.currentText() == design
    assert dialog.directory().absolutePath() == recent_category


def test_combo_display_restores_on_navigation(qapp, tmp_path):
    """폴더를 옮기면 Qt 가 콤보를 다시 채워 표시가 저절로 되돌아온다."""
    from qtpy.QtWidgets import QComboBox, QFileDialog

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    category = favorites.category_dir("설계")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    hooks_module.show_link_target_in_combo(dialog, Places(favorites=favorites))
    dialog.show()
    _spin(qapp, 300)

    combo = dialog.findChild(QComboBox, "lookInCombo")
    dialog.currentChanged.emit(os.path.join(category, "설계도.csv"))
    assert combo.currentText() == design

    # 다른 폴더로 이동 -> 원래대로 현재 폴더가 표시된다
    plain = str(tmp_path / "projA")
    dialog.setDirectory(plain)
    dialog.directoryEntered.emit(plain)
    _spin(qapp, 300)
    assert combo.currentText() == plain

    # 콤보를 갈아 끼우지 않으므로 위젯은 그대로 살아 있다
    assert combo.isVisible()
    dialog.close()


def test_widget_follows_links(qapp, tmp_path, monkeypatch):
    """FilePathEdit 이 연 다이얼로그에도 걸린다."""
    from qtpy.QtWidgets import QFileDialog

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    _design, _report, output = _make_tree(tmp_path)
    favorites.add("설계", output)
    link = os.path.join(favorites.category_dir("설계"), "산출물")

    shown = {}

    def fake_exec(self):
        self.setDirectory(link)
        self.directoryEntered.emit(link)
        shown["path"] = self.directory().absolutePath()
        return 0

    monkeypatch.setattr(QFileDialog, "exec_", fake_exec, raising=False)
    monkeypatch.setattr(QFileDialog, "exec", fake_exec, raising=False)

    FilePathEdit(mode="open_file", favorites=favorites).browse()
    assert shown["path"] == output



def test_follow_link_on_parent_disarms_without_navigation(qapp, tmp_path):
    """버튼을 눌렀다 끌어서 놓아(클릭 불발) 이동이 없으면 무장이 풀린다.

    장전된 채 남으면 **다음 평범한 이동을 가로채** 원본 부모로 순간이동했다.
    """
    from qtpy.QtWidgets import QFileDialog, QToolButton

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    category = favorites.category_dir("설계")
    other = tmp_path / "다른폴더"
    other.mkdir()

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    assert hooks_module.follow_link_on_parent(dialog, Places(favorites=favorites))
    button = dialog.findChild(QToolButton, "toParentButton")

    # 링크를 고르고 버튼을 눌렀지만 이동은 일어나지 않았다 (드래그로 놓침)
    dialog.currentChanged.emit(os.path.join(category, "설계도.csv"))
    button.pressed.emit()
    _spin(qapp, 50)                       # 이벤트 루프로 돌아오면 무장 해제

    # 그 뒤의 평범한 이동은 가로채이지 않는다
    dialog.setDirectory(str(other))
    dialog.directoryEntered.emit(str(other))
    assert dialog.directory().absolutePath() == str(other)
    dialog.deleteLater()


def test_follow_link_directories_checks_destination(qapp, tmp_path, monkeypatch):
    """링크 폴더로 들어가도 **원본이 열면 안 되는 자리면** 옮기지 않는다.

    setDirectory 는 그 폴더를 통째로 나열하고 directoryEntered 를 다시 내지
    않아 마지막 방어도 걸리지 않는다. 형제 함수(follow_link_on_parent)는 이미
    safe_isdir 로 감싸는데 여기만 무방비였다.
    """
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import safety

    work = tmp_path / "작업"
    work.mkdir()
    shallow = tmp_path / "user"                   # 원본이 얕은 자리라고 치자
    shallow.mkdir()

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    link = favorites.add("설계", str(shallow))
    places = Places(favorites=favorites)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(str(work))
    assert hooks_module.follow_link_directories(dialog, places)

    safety.configure(min_depth=safety.path_depth(str(shallow)) + 1)
    try:
        dialog.directoryEntered.emit(link)        # 링크 폴더로 들어갔다
        assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(
            str(work)
        )                                          # 얕은 원본으로 옮기지 않는다

        # 원본이 충분히 깊으면 평소대로 따라간다
        safety.reset()
        dialog.directoryEntered.emit(link)
        assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(
            str(shallow)
        )
    finally:
        safety.reset()
    dialog.deleteLater()


def test_follow_link_on_parent_respects_may_enter(qapp, tmp_path):
    """원본의 상위가 **열면 안 되는 자리**면 따라가지 않는다.

    safe_isdir 만 보면 min_depth 로 막아 둔 얕은 자리로 옮겨 간다 —
    setDirectory 는 directoryEntered 를 내지 않아 마지막 방어도 안 걸린다.
    """
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import safety

    shallow = tmp_path / "user"
    inner = shallow / "myaccount"
    inner.mkdir(parents=True)
    design = inner / "설계도.csv"
    design.write_text("x")

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    favorites.add("설계", str(design))
    places = Places(favorites=favorites)
    category = favorites.category_dir("설계")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(category)
    assert hooks_module.follow_link_on_parent(dialog, places)

    # 원본의 상위(inner)가 얕다고 보게 만든다
    safety.configure(min_depth=safety.path_depth(str(inner)) + 1)
    try:
        from qtpy.QtWidgets import QToolButton

        dialog.currentChanged.emit(os.path.join(category, "설계도.csv"))
        dialog.findChild(QToolButton, "toParentButton").pressed.emit()
        dialog.directoryEntered.emit(category)
        assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(
            category
        )                                   # 얕은 자리로 옮겨 가지 않았다
    finally:
        safety.reset()
    dialog.deleteLater()


def test_dialog_is_collected_after_close(qapp, tmp_path):
    """다이얼로그를 닫으면 실제로 수거된다 — 반복해서 열어도 쌓이지 않는다.

    "상위 폴더" 훅만 **자식 위젯**(버튼)의 시그널에 연결하는데, 클로저가
    다이얼로그를 직접 들고 있어 다이얼로그 -> 버튼 -> 연결 -> 클로저 ->
    다이얼로그 순환이 생겼다. 그 고리에 C++ 객체가 끼어 파이썬 gc 가 풀지
    못해, 다이얼로그를 여닫을 때마다 통째로 남았다(실측: 10회에 10개).
    """
    import gc
    import weakref

    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import CustomFileDialog

    # 바인딩마다 위젯 수명 관리가 다르다. PySide 는 show() 한 위젯의 파이썬
    # 래퍼를 오래 들고 있어 **순정 QFileDialog 도** 수거되지 않는다. 그런
    # 바인딩에서는 "우리가 더 붙잡는가"를 이 방법으로 물을 수 없다.
    plain = QFileDialog()
    plain.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    plain.show()
    _spin(qapp, 100)
    baseline = weakref.ref(plain)
    plain.done(0)
    plain.deleteLater()
    del plain
    _spin(qapp, 300)
    gc.collect()
    if baseline() is not None:
        pytest.skip("이 바인딩은 순정 다이얼로그도 수거하지 않는다(바인딩 특성)")

    design, _report, _output = _make_tree(tmp_path)
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    favorites.add("설계", design)
    recent = RecentStore(base_dir=str(tmp_path / "recent"))
    recent.record(design)

    def once():
        dialog = CustomFileDialog(
            None, mode="open_file", directory=os.path.dirname(design),
            favorites=favorites, recent=recent,
        )
        dialog.show()
        _spin(qapp, 100)
        ref = weakref.ref(dialog)
        dialog.done(0)
        dialog.deleteLater()
        del dialog
        _spin(qapp, 300)
        gc.collect()
        _spin(qapp, 50)
        return ref

    assert once()() is None                     # 한 번 열고 닫으면 사라진다

    # 여러 번 여닫아도 남지 않는다
    refs = [once() for _ in range(3)]
    gc.collect()
    assert [r() for r in refs] == [None, None, None]
