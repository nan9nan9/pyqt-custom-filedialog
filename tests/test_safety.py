"""죽은 네트워크 경로 방어 · 차단 경로(guarded_roots) · 자동완성 제한."""

import os
import shutil
import sys
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
from custom_file_dialog import safety
from custom_file_dialog import mounts as safety_mounts
from custom_file_dialog import reach as safety_reach
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


def test_safety_mount_lookup(dead_nfs):
    """마운트 표만 보고 원격 여부와 서버를 알아낸다(파일시스템 미접근)."""
    safety = dead_nfs["safety"]
    mount = dead_nfs["mount"]

    assert safety.is_remote(os.path.join(mount, "a", "b.csv"))
    assert not safety.is_remote("/etc/hosts")
    assert safety.mount_for(os.path.join(mount, "x"))[1] == "nfs4"
    assert safety.server_of("nfs1.corp:/export/proj") == "nfs1.corp"
    assert safety.server_of("//winsrv/share") == "winsrv"
    assert safety.server_of("[fe80::1]:/export") == "fe80::1"    # IPv6 는 대괄호부터
    assert safety.server_of("user@host:/dir") == "host"
    assert safety.server_of("/dev/sda1") is None
    assert safety.mount_for("") is None


def test_safety_blocks_on_dead_server(dead_nfs):
    """서버가 막혀 있으면 소켓 프로브에서 걸러내고 stat 을 시도하지 않는다."""
    safety = dead_nfs["safety"]
    state = dead_nfs["state"]
    target = os.path.join(dead_nfs["mount"], "proj", "a.csv")

    started = time.time()
    assert not safety.is_reachable(target, timeout=0.2)
    assert time.time() - started < 2            # 곧바로 판정
    assert state["probes"]                      # 프로브는 했고
    assert state["stat_calls"] == 0             # stat 은 아예 안 했다

    # 판정은 마운트 단위로 캐시되어 다시 두드리지 않는다
    count = len(state["probes"])
    assert not safety.is_reachable(target, timeout=0.2)
    assert len(state["probes"]) == count


def test_safety_timeout_when_probe_passes(dead_nfs):
    """프로브는 통과했는데 stat 이 안 돌아오면 타임아웃으로 끊는다."""
    safety = dead_nfs["safety"]
    state = dead_nfs["state"]
    state["probe_ok"] = True                    # 소켓은 열려 있지만
    state["stat_hangs"] = True                  # stat 은 안 돌아온다
    target = os.path.join(dead_nfs["mount"], "proj")

    started = time.time()
    assert not safety.is_reachable(target, timeout=0.2)
    elapsed = time.time() - started
    assert 0.1 < elapsed < 3                    # 제한 시간만 기다린다
    assert state["stat_calls"] == 1             # 실제로 시도는 했다

    # 멈춘 스레드는 남지만 호출한 쪽은 돌아왔다(GIL 이 풀리므로 GUI 도 산다)
    assert safety.pending_checks() >= 1


def test_safety_local_paths_are_fast(dead_nfs):
    """로컬 경로는 프로브도 stat 도 없이 곧바로 통과한다."""
    safety = dead_nfs["safety"]
    state = dead_nfs["state"]

    assert safety.is_reachable("/etc/hosts")
    assert state["probes"] == []
    assert safety.safe_isdir("/etc") is True
    assert safety.safe_isfile("/etc") is False


def test_guarded_root_blocks_itself_only(guarded_root):
    """그 자리 자체만 막고, 하위 경로는 평소대로 쓴다."""
    from custom_file_dialog import safety


    assert safety.guarded_roots() == [os.path.normpath(guarded_root)]

    assert safety.is_guarded(guarded_root)
    assert safety.is_guarded(guarded_root + os.sep)          # 끝의 / 는 무시
    assert not safety.is_guarded(os.path.join(guarded_root, "myaccount"))
    assert not safety.is_guarded(guarded_root + "s")         # 이름만 비슷한 건 아님

    # 접근 판정과 os.path 대체 함수에도 그대로 반영된다
    assert not safety.is_reachable(guarded_root)
    assert safety.is_reachable(os.path.join(guarded_root, "myaccount"))
    assert safety.safe_isdir(guarded_root) is False          # 실제로는 폴더지만 안 만진다
    assert safety.safe_isdir(os.path.join(guarded_root, "myaccount")) is True


def test_guarded_root_in_validation(qapp, guarded_root):
    """차단 경로를 입력하면 '없는 경로'로 보고, 하위 경로는 정상 판정한다."""
    edit = FilePathEdit(mode="directory")

    edit.set_path(guarded_root)
    assert not edit.is_valid()

    edit.set_path(os.path.join(guarded_root, "myaccount"))
    assert edit.is_valid()


def test_guarded_root_not_used_as_start_dir(qapp, guarded_root, tmp_path):
    """다이얼로그가 차단 경로에서 열리지 않는다."""
    alive = str(tmp_path / "정상")
    os.mkdir(alive)

    resolved = dialog_module.resolve_start_dir(
        [], start_dir=guarded_root, last_dir=alive, mode=SelectMode.OPEN_FILE, timeout=1.0
    )
    assert resolved == alive


def test_guarded_root_not_listed_by_completer(qapp, guarded_root):
    """자동완성이 차단 경로의 목록을 읽지 않는다(하위는 읽는다)."""
    edit = FilePathEdit(mode="open_file")
    model = edit.line_edit.completer().model()
    model.setRootPath("")

    def rows(path):
        index = model.index(path)
        model.hasChildren(index)
        model.canFetchMore(index)
        model.fetchMore(index)
        _spin(qapp, 900)
        return model.rowCount(model.index(path))

    assert len(os.listdir(guarded_root)) == 3
    assert rows(guarded_root) == 0                   # 3개가 있어도 읽지 않는다

    inner = os.path.join(guarded_root, "myaccount")
    assert rows(inner) == len(os.listdir(inner))     # 하위는 정상


def test_guarded_model_blocks_listing(qapp, guarded_root):
    """차단 경로의 목록을 아예 요청하지 않는다(하위는 정상)."""
    from custom_file_dialog import GuardedFileSystemModel

    model = GuardedFileSystemModel()
    model.setRootPath("")

    def rows(path):
        index = model.index(path)
        model.hasChildren(index)
        model.canFetchMore(index)
        model.fetchMore(index)
        _spin(qapp, 800)
        return model.rowCount(model.index(path))

    assert len(os.listdir(guarded_root)) == 3
    assert rows(guarded_root) == 0                   # 3개가 있어도 읽지 않는다

    inner = os.path.join(guarded_root, "myaccount")
    assert rows(inner) == len(os.listdir(inner))     # 하위는 정상

    # 판정은 safety 설정을 그대로 따르므로, 해제하면 다시 읽는다
    index = model.index(guarded_root)
    assert not model.hasChildren(index)
    assert not model.canFetchMore(index)


def test_path_depth(qapp):
    """깊이는 루트에서부터 센다."""
    from custom_file_dialog import safety

    assert safety.path_depth("/") == 0
    assert safety.path_depth("/user") == 1
    assert safety.path_depth("/user/") == 1                  # 끝의 / 는 무시
    assert safety.path_depth("/user/myaccount") == 2
    assert safety.path_depth("/user/myaccount/proj") == 3
    assert safety.path_depth("") == 0
    # 상대 경로는 절대 경로로 편 뒤에 센다
    assert safety.path_depth("myaccount") == safety.path_depth(os.getcwd()) + 1


def test_min_depth_default_off(qapp):
    """지정하지 않으면 아무 자리도 얕다고 보지 않는다."""
    from custom_file_dialog import safety

    safety.reset()
    assert safety.min_depth() == 0
    assert not safety.is_too_shallow("/")
    assert not safety.is_too_shallow("/user")


def test_min_depth_marks_shallow_paths(shallow_tree):
    """min_depth 보다 얕은 자리만 "나열 금지"로 본다."""
    from custom_file_dialog import safety

    root, depth = shallow_tree
    safety.configure(min_depth=depth + 1)

    assert safety.min_depth() == depth + 1
    assert safety.is_too_shallow(root)                       # 딱 한 단계 모자란다
    assert safety.is_too_shallow(os.path.dirname(root))      # 그 위는 더 얕다
    assert not safety.is_too_shallow(os.path.join(root, "myaccount"))

    # 나열만 막는 설정이라 경로 자체의 접근 판정은 건드리지 않는다
    assert safety.is_reachable(root)
    assert safety.safe_isdir(root) is True


def test_min_depth_blocks_completer_listing(qapp, shallow_tree):
    """`/user/my` 처럼 쳐도 그 폴더를 읽지 않는다(한 단계 아래는 정상)."""
    from custom_file_dialog import safety

    root, depth = shallow_tree
    edit = FilePathEdit(mode="open_file")
    model = edit.line_edit.completer().model()
    model.setRootPath("")

    def rows(path):
        index = model.index(path)
        model.hasChildren(index)
        model.canFetchMore(index)
        model.fetchMore(index)
        _spin(qapp, 900)
        return model.rowCount(model.index(path))

    safety.configure(min_depth=depth + 1)
    assert len(os.listdir(root)) == 3
    assert rows(root) == 0                                   # 3개가 있어도 읽지 않는다

    inner = os.path.join(root, "myaccount")
    assert rows(inner) == len(os.listdir(inner))             # 한 단계 아래는 정상


def test_min_depth_completion_candidates(qapp, shallow_tree):
    """자동완성 후보 자체가 뜨지 않는다 — 껐을 때와 비교한다."""
    from custom_file_dialog import safety

    root, depth = shallow_tree

    def candidates(prefix):
        edit = FilePathEdit(mode="open_file")
        completer = edit.line_edit.completer()
        # 부모 폴더를 모델에 알린 뒤 완성을 물어본다(실제 입력과 같은 순서)
        completer.model().setRootPath(os.path.dirname(prefix))
        _spin(qapp, 900)
        completer.setCompletionPrefix(prefix)
        return sorted(
            completer.completionModel().index(row, 0).data()
            for row in range(completer.completionCount())
        )

    safety.reset()
    assert candidates(os.path.join(root, "my")) == ["myaccount"]

    safety.configure(min_depth=depth + 1)
    assert candidates(os.path.join(root, "my")) == []
    # 한 단계 아래에서는 그대로 완성된다
    assert candidates(os.path.join(root, "myaccount", "p")) == ["proj"]


def test_allow_listing_off_blocks_every_depth(qapp, tmp_path):
    """allow_listing=False 면 깊이와 무관하게 어떤 폴더도 읽지 않는다."""
    from custom_file_dialog import safety

    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    for name in ("x", "y", "z"):
        (deep / name).mkdir()

    edit = FilePathEdit(mode="open_file")
    model = edit.line_edit.completer().model()
    model.setRootPath("")

    def rows(path):
        index = model.index(path)
        model.hasChildren(index)
        model.canFetchMore(index)
        model.fetchMore(index)
        _spin(qapp, 900)
        return model.rowCount(model.index(path))

    try:
        safety.configure(allow_listing=False)
        assert not safety.listing_allowed()
        assert not safety.may_list(str(deep))
        # 깊이가 충분해도(min_depth 는 꺼져 있다) 읽지 않는다
        assert not safety.is_too_shallow(str(deep))
        assert rows(str(deep)) == 0

        safety.configure(allow_listing=True)
        assert rows(str(deep)) == 3
    finally:
        safety.reset()


def test_allow_listing_leaves_paths_usable(qapp, tmp_path):
    """나열만 막는다 — 경로를 직접 넣어 쓰는 것은 그대로다."""
    from custom_file_dialog import safety

    target = tmp_path / "data.csv"
    target.write_text("x", encoding="utf-8")

    try:
        safety.configure(allow_listing=False)
        edit = FilePathEdit(mode="open_file")
        edit.set_path(str(target))
        assert edit.is_valid()                       # 유효성 판정은 그대로
        assert edit.path() == str(target)
        assert safety.is_reachable(str(tmp_path))    # 접근 판정도 그대로
        assert safety.safe_isdir(str(tmp_path)) is True
    finally:
        safety.reset()


def test_set_completer_toggles_at_runtime(qapp, tmp_path):
    """위젯 하나만 자동완성을 껐다 켤 수 있다."""
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()

    edit = FilePathEdit(mode="open_file")
    assert edit.completer_enabled()
    assert edit.line_edit.completer() is not None

    edit.set_completer(False)
    assert not edit.completer_enabled()
    assert edit.line_edit.completer() is None
    edit.set_completer(False)                        # 중복 호출도 안전
    assert not edit.completer_enabled()

    # 껐어도 경로 입력과 유효성은 그대로
    edit.set_path(str(tmp_path))
    assert edit.path() == str(tmp_path)

    edit.set_completer(True)
    assert edit.completer_enabled()
    model = edit.line_edit.completer().model()
    model.setRootPath("")
    index = model.index(str(tmp_path))
    model.fetchMore(index)
    _spin(qapp, 900)
    assert model.rowCount(model.index(str(tmp_path))) == 2


def test_completer_off_from_constructor(qapp):
    """completer=False 면 처음부터 만들지 않는다."""
    edit = FilePathEdit(mode="open_file", completer=False)
    assert not edit.completer_enabled()
    assert edit.line_edit.completer() is None


def test_allow_listing_alone_installs_completer_guard(qapp):
    """차단 경로도 min_depth 도 없이 allow_listing 만으로 다이얼로그를 지킨다."""
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import GuardedFileSystemModel, safety

    from custom_file_dialog.guard import _TypingGuard

    try:
        safety.configure(allow_listing=False)
        dialog = QFileDialog()
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        installed = hooks_module.guard_dialog(dialog)
        # 자동완성 모델 + 타이핑 가드까지만 — "못 들어가게" 장치는 걸지 않는다
        assert installed[0] == "completer"
        assert all(
            item == "completer" or isinstance(item, _TypingGuard)
            for item in installed
        )

        name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
        model = name_edit.completer().model()
        assert isinstance(model, GuardedFileSystemModel)
        assert not model.canFetchMore(model.index(str(QDir.homePath())))
        dialog.deleteLater()
    finally:
        safety.reset()


def test_min_depth_alone_installs_completer_guard(qapp, shallow_tree):
    """차단 경로가 없어도 min_depth 만으로 다이얼로그 자동완성을 갈아 끼운다."""
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import GuardedFileSystemModel, safety

    _root, depth = shallow_tree
    safety.configure(guarded_roots=[], min_depth=depth + 1)
    assert safety.guarded_roots() == []

    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    installed = hooks_module.guard_dialog(dialog)

    # min_depth 만 켜도 전부 건다 — 얕은 자리는 **들어가는 것만으로** 통째로
    # 나열되므로 확정뿐 아니라 이동(더블클릭 · 콤보 · 상위 폴더)도 막아야 한다.
    from custom_file_dialog.guard import (
        _AcceptBlocker,
        _ItemBlocker,
        _ParentBlocker,
        _TypingGuard,
    )

    assert installed[0] == "completer"
    assert any(isinstance(item, _TypingGuard) for item in installed)
    assert any(isinstance(item, _AcceptBlocker) for item in installed)
    assert any(isinstance(item, _ItemBlocker) for item in installed)
    assert any(isinstance(item, _ParentBlocker) for item in installed)
    assert "bounce" in installed
    name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
    assert isinstance(name_edit.completer().model(), GuardedFileSystemModel)

    dialog.deleteLater()


def test_min_depth_off_installs_nothing(qapp, monkeypatch):
    """설정도 automount 도 없으면 다이얼로그에 아무것도 걸지 않는다."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import safety

    safety.reset()
    # autofs 가 있는 시스템에서는 설정 없이도 걸리므로, "없는 시스템"을 고정한다
    monkeypatch.setattr(safety_mounts, "has_automounts", lambda: False)
    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    assert hooks_module.guard_dialog(dialog) == []
    dialog.deleteLater()


def test_guard_dialog_installs_hooks(qapp, guarded_root):
    """다이얼로그의 자동완성 모델을 갈아 끼우고 이벤트 필터를 건다."""
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import GuardedFileSystemModel, guard_dialog

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(os.path.dirname(guarded_root))

    name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
    assert not isinstance(name_edit.completer().model(), GuardedFileSystemModel)

    installed = guard_dialog(dialog)
    assert "completer" in installed
    assert "bounce" in installed
    assert isinstance(name_edit.completer().model(), GuardedFileSystemModel)
    dialog.close()


def test_guard_dialog_noop_without_guarded_roots(qapp, tmp_path, monkeypatch):
    """차단 경로가 없으면(automount 도 없으면) 아무것도 걸지 않는다."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import guard_dialog, safety

    safety.configure(guarded_roots=[])
    monkeypatch.setattr(safety_mounts, "has_automounts", lambda: False)
    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(str(tmp_path))
    assert guard_dialog(dialog) == []
    dialog.close()


def test_enter_blocker_swallows_open_events(qapp, guarded_root):
    """차단 경로 항목의 더블클릭/Enter 이벤트를 삼킨다(하위/일반은 통과)."""
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QKeyEvent, QMouseEvent
    from qtpy.QtWidgets import QFileDialog, QTreeView

    from custom_file_dialog import guard_dialog
    from custom_file_dialog.guard import _ItemBlocker

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(os.path.dirname(guarded_root))
    dialog.show()
    _spin(qapp, 500)

    installed = guard_dialog(dialog)
    blockers = [h for h in installed if isinstance(h, _ItemBlocker)]
    assert blockers

    tree = dialog.findChild(QTreeView, "treeView")
    blocker = [b for b in blockers if b._view is tree][0]
    model, root_index = tree.model(), tree.rootIndex()
    rows = {
        model.index(r, 0, root_index).data(): model.index(r, 0, root_index)
        for r in range(model.rowCount(root_index))
    }
    assert "user" in rows, sorted(rows)

    def double_click(index):
        tree.scrollTo(index)
        point = tree.visualRect(index).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(point),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return blocker.eventFilter(tree.viewport(), event)

    def press_enter(index):
        tree.setCurrentIndex(index)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        return blocker.eventFilter(tree, event)

    # 차단 경로 -> 이벤트를 삼킨다(= 진입 안 됨)
    assert double_click(rows["user"]) is True
    assert press_enter(rows["user"]) is True
    assert blocker.blocked                       # 무엇을 막았는지 기록된다

    # 차단 대상이 아닌 항목은 그대로 통과시킨다
    other = [name for name in rows if name != "user"]
    if other:
        assert double_click(rows[other[0]]) is False
        assert press_enter(rows[other[0]]) is False
    dialog.close()


def test_combo_blocker_swallows_guarded_entry(qapp, guarded_root):
    """"Look in" 드롭다운에서 차단 경로를 고를 수 없다(다른 항목은 정상)."""
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QKeyEvent, QMouseEvent
    from qtpy.QtWidgets import QComboBox

    from custom_file_dialog.guard import _ItemBlocker

    inner = os.path.join(guarded_root, "myaccount")
    dialog, installed = _guarded_dialog_in(qapp, inner)
    combo = dialog.findChild(QComboBox, "lookInCombo")
    blocker = [
        h
        for h in installed
        if isinstance(h, _ItemBlocker) and h._view is combo.view()
    ][0]
    combo.showPopup()
    _spin(qapp, 300)
    view = combo.view()
    entries = {combo.itemText(i): combo.model().index(i, 0) for i in range(combo.count())}
    combo.hidePopup()

    # 현재 폴더가 /user/myaccount 이므로 경로 체인에 /user 가 들어 있다
    guarded = [t for t in entries if os.path.normpath(t) == os.path.normpath(guarded_root)]
    assert guarded, sorted(entries)

    def click(index):
        point = view.visualRect(index).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(point),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return blocker.eventFilter(view.viewport(), event)

    def enter(index):
        view.setCurrentIndex(index)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        return blocker.eventFilter(view, event)

    assert click(entries[guarded[0]]) is True       # 차단 경로 -> 삼킴
    assert enter(entries[guarded[0]]) is True

    others = [t for t in entries if t not in guarded]
    for text in others:
        assert click(entries[text]) is False        # 나머지는 그대로 통과
    dialog.close()


def test_accept_blocker_swallows_guarded_path(qapp, guarded_root):
    """파일 이름 칸에 차단 경로를 치고 Enter/열기 로 확정할 수 없다."""
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QKeyEvent, QMouseEvent
    from qtpy.QtWidgets import QDialogButtonBox, QLineEdit

    from custom_file_dialog.guard import _AcceptBlocker

    inner = os.path.join(guarded_root, "myaccount")
    dialog, installed = _guarded_dialog_in(qapp, inner)
    blocker = [h for h in installed if isinstance(h, _AcceptBlocker)][0]

    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    box = dialog.findChild(QDialogButtonBox, "buttonBox")
    button = box.button(QDialogButtonBox.StandardButton.Open)

    def press_enter():
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        return blocker.eventFilter(edit, event)

    def click_open():
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return blocker.eventFilter(button, event)

    # 절대 경로로 친 경우
    edit.setText(guarded_root)
    assert press_enter() is True
    assert click_open() is True

    # 상대 경로 ".." 도 /user 로 풀리므로 막힌다
    edit.setText("..")
    assert press_enter() is True

    # 지금 폴더(차단 경로 **안**) 기준의 이름들은 그대로 통과한다
    for text in ("proj", "보고서.csv"):
        edit.setText(text)
        assert press_enter() is False, text
        assert click_open() is False, text

    # 차단 경로 **바로 아래**는 구분자 없이는 막히고, 붙이면 열린다
    sibling = os.path.join(guarded_root, "alice")
    edit.setText(sibling)
    assert press_enter() is True
    edit.setText(sibling + os.sep)
    assert press_enter() is False

    assert blocker.blocked                      # 무엇을 막았는지 기록된다
    dialog.close()


def test_blockers_survive_deleted_widgets(qapp, guarded_root):
    """다이얼로그가 닫히는 중 이벤트가 와도 죽지 않는다(회귀 테스트)."""
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent

    from custom_file_dialog.guard import _AcceptBlocker, _ItemBlocker

    inner = os.path.join(guarded_root, "myaccount")
    dialog, installed = _guarded_dialog_in(qapp, inner)
    blockers = [h for h in installed if isinstance(h, (_ItemBlocker, _AcceptBlocker))]
    assert blockers

    dialog.close()
    dialog.deleteLater()
    del dialog
    _spin(qapp, 300)

    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(1, 1),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    for blocker in blockers:
        assert blocker.eventFilter(None, event) is False    # 예외 없이 통과


def test_validate_paths_with_timeout(dead_nfs):
    """유효성 검사가 죽은 경로에서 멈추지 않고 '없음'으로 판정한다."""
    target = os.path.join(dead_nfs["mount"], "proj", "a.csv")

    started = time.time()
    ok, reason = validate_paths([target], mode=SelectMode.OPEN_FILE, timeout=0.2)
    assert time.time() - started < 3
    assert not ok and "존재하지 않습니다" in reason


def test_resolve_start_dir_skips_dead_mount(dead_nfs, tmp_path):
    """다이얼로그 시작 폴더로 죽은 마운트를 고르지 않는다."""
    dead = os.path.join(dead_nfs["mount"], "proj")
    alive = str(tmp_path / "정상")
    os.mkdir(alive)

    started = time.time()
    resolved = dialog_module.resolve_start_dir(
        [], start_dir=dead, last_dir=alive, mode=SelectMode.OPEN_FILE, timeout=0.2
    )
    assert time.time() - started < 3
    assert resolved == alive                    # 죽은 곳을 건너뛴다


def test_widget_path_timeout_is_on_by_default(qapp, monkeypatch):
    """안전 확인은 기본으로 켜져 있고, 로컬 경로에는 부담을 주지 않는다."""
    from custom_file_dialog import safety

    edit = FilePathEdit(mode="open_file")
    assert edit.path_timeout() == safety.DEFAULT_TIMEOUT

    # 로컬 경로는 스레드를 만들지 않고 그대로 확인한다
    # (Qt 가 스스로 만드는 감시 스레드와 섞이지 않게, 우리 호출만 센다)
    spawned = []
    real = safety.call_with_timeout
    monkeypatch.setattr(
        safety_reach,
        "call_with_timeout",
        lambda *a, **k: (spawned.append(a), real(*a, **k))[1],
    )

    for _ in range(50):
        edit.set_path("/etc/hosts")
        edit.set_path("/etc/없는파일")

    assert spawned == []                    # 스레드를 아예 안 만든다
    assert edit.path() == "/etc/없는파일"
    assert not edit.is_valid()              # 검사 자체는 정상 동작


def test_widget_path_timeout(qapp, dead_nfs, monkeypatch):
    """FilePathEdit 이 안전 확인을 켜고 다이얼로그에도 전달한다."""
    target = os.path.join(dead_nfs["mount"], "proj", "a.csv")

    edit = FilePathEdit(mode="open_file", path_timeout=0.2)
    assert edit.path_timeout() == 0.2

    started = time.time()
    edit.set_path(target)                       # 여기서 멈추면 안 된다
    assert time.time() - started < 3
    assert not edit.is_valid()

    seen = {}
    monkeypatch.setattr(
        dialog_module,
        "exec_file_dialog",
        lambda **kw: (seen.update(kw), ([], ""))[1],
    )
    edit.browse()
    assert not seen["directory"].startswith(dead_nfs["mount"])   # 죽은 곳에서 안 연다

    # 끄면 평범한 os.path 확인으로 돌아간다
    edit.set_path_timeout(None)
    assert edit.path_timeout() is None



def test_mountinfo_unescapes_special_characters(monkeypatch, tmp_path):
    """mountinfo 의 8진수 이스케이프(공백=\\040 등)를 풀어서 마운트를 맞춘다.

    풀지 않으면 공백이 든 마운트는 어떤 경로와도 못 맞춰 원격 판별이 조용히
    실패하고, 그 마운트에는 안전장치가 걸리지 않았다.
    """
    from custom_file_dialog import safety

    fake = tmp_path / "mountinfo"
    fake.write_text(
        "36 25 0:32 / /mnt/my\\040share rw - nfs4 server:/with\\040space rw\n"
        "37 25 0:33 / /mnt/plain rw - nfs4 server:/export rw\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(safety_mounts, "MOUNTINFO", str(fake))
    safety.clear_cache()
    try:
        mount = safety.mount_for("/mnt/my share/a.csv")
        assert mount == ("/mnt/my share", "nfs4", "server:/with space")
        assert safety.is_remote("/mnt/my share/a.csv")
        assert safety.mount_for("/mnt/plain/a")[0] == "/mnt/plain"
    finally:
        safety.clear_cache()        # 가짜 표가 캐시에 남지 않게


def test_accept_blocker_lets_clicks_into_edit(qapp, guarded_root):
    """차단 경로가 입력돼 있어도 **입력창 클릭은** 삼키지 않는다.

    클릭으로 "확정"이 되는 건 열기/저장 버튼뿐이다. 입력창 클릭까지 삼키면
    사용자가 경로를 고치려고 칸을 클릭하는 것조차 안 된다.
    """
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit, QPushButton

    from custom_file_dialog.guard import _AcceptBlocker

    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    button = QPushButton(dialog)             # 열기 버튼 대역
    blocker = _AcceptBlocker(dialog, edit, dialog)
    edit.setText(guarded_root)

    def release():
        return QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(5, 5),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    assert not blocker.eventFilter(edit, release())      # 입력창 클릭은 통과
    assert blocker.eventFilter(button, release())        # 버튼 클릭은 차단
    dialog.deleteLater()


def test_probe_port_follows_fstype(monkeypatch, tmp_path):
    """서버 프로브는 마운트 종류에 맞는 포트로만 두드린다.

    모든 원격 종류에 NFS 포트(2049)를 두드리면, 멀쩡한 CIFS/SSHFS 서버가
    거부(그 포트를 안 듣는다)해서 "죽었다"로 오판됐다 — 유효한 경로가
    "없는 경로"로 표시되고 시작 폴더에서도 빠졌다.
    """
    from custom_file_dialog import safety

    probed = []
    monkeypatch.setattr(
        safety_reach, "probe_host", lambda host, port, timeout=None: (
            probed.append((host, port)), True)[1]
    )
    stats = []

    def fake_call(func, *args, **kwargs):
        """프로브는 실제로 부르고, stat 은 부른 사실만 남긴다.

        프로브도 스레드+타임아웃 안에서 돈다(이름 조회가 타임아웃 밖이라
        GUI 가 멈추던 것을 막기 위해). 그래서 이 가짜도 함수를 통과시켜야
        어느 포트를 두드렸는지 볼 수 있다.
        """
        if func is safety_reach.probe_host:
            return True, func(*args)
        stats.append(args)
        return True, None

    monkeypatch.setattr(safety_reach, "call_with_timeout", fake_call)

    assert safety.self_check("/mnt/win", "//winsrv/share", 0.1, fstype="cifs")
    assert probed == [("winsrv", 445)]                   # 2049 가 아니라 445

    probed.clear()
    assert safety.self_check("/mnt/ssh", "user@box:/dir", 0.1, fstype="fuse.sshfs")
    assert probed == [("box", 22)]

    # 포트를 모르는 종류는 서버를 두드리지 않고 stat 으로만 판정한다
    probed.clear()
    stats.clear()
    assert safety.self_check("/mnt/gl", "gl1:/vol", 0.1, fstype="glusterfs")
    assert probed == []
    assert stats                                          # stat 은 돌았다


def test_is_reachable_passes_fstype(monkeypatch, tmp_path):
    """is_reachable 이 마운트 종류를 self_check 까지 전달한다."""
    from custom_file_dialog import safety

    mountpoint = str(tmp_path / "win")
    os.mkdir(mountpoint)
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [(mountpoint, "cifs", "//winsrv/share")],
    )
    seen = {}

    def fake_self_check(mp, source, timeout=None, fstype=None):
        seen.update(fstype=fstype, source=source)
        return True

    monkeypatch.setattr(safety_reach, "self_check", fake_self_check)
    safety.clear_cache()
    try:
        assert safety.is_reachable(os.path.join(mountpoint, "a.txt"), use_cache=False)
        assert seen["fstype"] == "cifs"
    finally:
        safety.clear_cache()


def test_mountinfo_parser_survives_malformed_lines(monkeypatch, tmp_path):
    """깨진 mountinfo 줄에서 예외가 나면 안 된다(퍼징으로 발견).

    구분자("-")가 마운트지점(fields[4])보다 앞에 오는 줄에서 IndexError 가
    났다. /proc 이 아니라도 MOUNTINFO 를 갈아 끼울 수 있으므로 어떤 입력에도
    조용히 건너뛰어야 한다.
    """
    from custom_file_dialog import safety

    fake = tmp_path / "mountinfo"
    fake.write_text(
        "- - -\n"                                     # 구분자가 맨 앞
        "a - b c\n"                                   # 구분자 뒤는 있는데 앞이 짧다
        "짧은줄\n"
        "\n"
        "36 25 0:32 / /mnt/ok rw - nfs4 srv:/e rw\n"   # 정상 줄 하나
        "37 25 0:33 / /mnt/short rw - nfs4\n",         # 구분자 뒤가 짧다
        encoding="utf-8",
    )
    monkeypatch.setattr(safety_mounts, "MOUNTINFO", str(fake))
    safety.clear_cache()
    try:
        mounts = safety.iter_mounts(refresh=True)
        assert mounts == [("/mnt/ok", "nfs4", "srv:/e")]   # 정상 줄만 남는다
        assert safety.mount_for("/mnt/ok/a")[0] == "/mnt/ok"
    finally:
        safety.clear_cache()


# ---------------------------------------------------------------------------
# 키 입력마다의 자동 stat 방어 — may_stat · 타이핑 가드 · automount 자동 인지
# ---------------------------------------------------------------------------


def test_may_stat_guarded_parent(guarded_root):
    """부모가 차단 경로면 자동 stat 금지 — 하위와 그 자리 자체는 허용."""
    from custom_file_dialog import safety

    assert not safety.may_stat(os.path.join(guarded_root, "j"))
    # 한 단계 아래(실제로 쓰는 홈)는 평소대로
    assert safety.may_stat(os.path.join(guarded_root, "myaccount", "f.csv"))
    # 차단 경로 "자체"의 stat 은 마운트를 부르지 않는다(나열과 다르다)
    assert safety.may_stat(guarded_root)


def test_may_stat_min_depth(shallow_tree):
    """깊이가 min_depth 보다 **작거나 같으면** 자동 stat 을 아예 하지 않는다.

    min_depth=2 기준으로 ``/user`` (1) 도 ``/user/my`` (2) 도 디스크를 만지지
    않고, ``/user/myaccount/x`` (3) 부터 평소대로 확인한다.
    """
    from custom_file_dialog import safety

    root, depth = shallow_tree
    safety.configure(min_depth=depth + 1)
    assert not safety.may_stat(root)                             # 깊이 < limit
    assert not safety.may_stat(os.path.join(root, "j"))          # 깊이 == limit
    assert not safety.may_stat(os.path.join(root, "je"))         # 깊이 == limit
    assert safety.may_stat(os.path.join(root, "myaccount", "proj"))  # 깊이 > limit


def test_automount_autodetected(monkeypatch, tmp_path):
    """autofs 마운트 지점은 설정 없이도 나열·자동 stat 이 막힌다."""
    from custom_file_dialog import safety

    root = tmp_path / "user"
    (root / "myaccount").mkdir(parents=True)
    mounted = str(root / "myaccount")

    safety.reset()
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (str(root), "autofs", "auto.user"),
            (mounted, "nfs4", "srv:/export/myaccount"),   # 이미 붙은 하위 마운트
        ],
    )
    try:
        assert safety.has_automounts()
        assert safety.on_automount(str(root))
        assert safety.on_automount(str(root / "아직안붙음"))      # 지점 아래도
        assert not safety.on_automount(mounted)                  # 붙은 하위는 아님
        assert not safety.on_automount(str(tmp_path))            # 밖은 아님

        assert not safety.may_list(str(root))                    # 나열 금지
        assert not safety.may_list(str(root / "아직안붙음"))      # 안 붙은 하위도
        assert not safety.may_stat(str(root / "j"))              # 자식 stat 금지
        assert safety.may_stat(os.path.join(mounted, "a.csv"))   # 붙은 하위는 평소대로

        # 만지는 판정도 같은 규칙 — autofs 위는 디스크 접근 없이 즉시 False
        assert not safety.is_reachable(str(root / "j"))

        # 이미 붙은 하위(nfs4)는 **정책이 막지 않는다.** 예전에는 여기서
        # safe_isdir 가 False 라고 단정했는데, 그것은 정책이 아니라 지어낸
        # 호스트가 이 컨테이너에서 DNS 를 못 찾아 난 결과였다(회사망처럼
        # 짧은 이름이 풀리는 곳에서는 True 가 되어 깨진다). 서버가 살아 있으면
        # 평소대로 동작하는 것이 맞다 — 프로브를 통제해서 그것을 못박는다.
        monkeypatch.setattr(safety_reach, "probe_host", lambda *a, **k: True)
        safety.clear_cache()
        assert safety.safe_isdir(mounted) is True
    finally:
        safety.clear_cache()


def test_safe_call_never_touches_autofs(monkeypatch, tmp_path):
    """autofs 위 경로는 safe_* 가 **아예 만지지 않는다** — 스레드도 안 만든다.

    autofs 는 원격 종류가 아니라서 예전에는 "로컬"로 보고 GUI 스레드에서 곧바로
    stat 했다 — automounter 뒷단(LDAP/NIS)이 죽어 있으면 그대로 멈췄다. 확인
    스레드를 만들어 두드리는 것도 마운트 시도라, 이제 즉시 default 로 판정한다.
    """
    from custom_file_dialog import safety

    mountpoint = str(tmp_path / "user")
    os.mkdir(mountpoint)

    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (mountpoint, "autofs", "auto.user"),
        ],
    )

    touched = []
    real_stat = os.stat

    def counting_stat(path, *args, **kwargs):
        if str(path).startswith(mountpoint):
            touched.append(str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", counting_stat)
    try:
        before = safety.pending_checks()
        start = time.time()
        assert safety.safe_exists(os.path.join(mountpoint, "j"), timeout=0.2) is False
        assert not safety.is_reachable(os.path.join(mountpoint, "j"))
        # 기다리지 않고 곧바로 돌아왔는지만 본다. 세 번을 정말 두드렸다면
        # 제한 시간(0.2초)씩 0.6초가 걸린다 — 그 절반을 경계로 잡는다.
        # (0.1초로 잡았더니 머신이 바쁠 때 흔들렸다. "안 두드렸다"는 아래
        #  stat_calls 단언이 이미 못박고 있으므로 여기는 여유를 준다.)
        assert time.time() - start < 0.3        # 기다림 없이 즉시
        assert touched == []                    # 디스크 접근 0회
        assert safety.pending_checks() == before    # 스레드도 0개
    finally:
        safety.clear_cache()


def test_hung_mount_spawns_only_one_thread(monkeypatch, tmp_path):
    """죽은 마운트를 키 입력마다 확인해도 멈춘 스레드는 마운트당 하나뿐이다.

    입력 한 자마다 확인 스레드가 생겨 쌓이면 그것대로 문제다. 멈춘 확인이
    걸려 있는 마운트는 다시 두드리지 않고 곧바로 실패로 판정해야 한다.
    """
    from custom_file_dialog import safety

    mountpoint = str(tmp_path / "nfs")
    os.mkdir(mountpoint)

    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (mountpoint, "nfs4", "srv:/export"),
        ],
    )
    monkeypatch.setattr(safety_reach, "probe_host", lambda *a, **k: True)

    stat_calls = []
    real_stat = os.stat

    def hanging_stat(path, *args, **kwargs):
        text = str(path)
        if text.startswith(mountpoint + os.sep):
            stat_calls.append(text)
            time.sleep(0.8)                     # 제한 시간(0.2초)보다 길게
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", hanging_stat)
    try:
        # 첫 확인: 실제로 두드리고(스레드 1) 제한 시간 뒤 실패로 돌아온다
        assert safety.safe_isfile(os.path.join(mountpoint, "a"), timeout=0.2) is False
        assert len(stat_calls) == 1

        # 그 스레드가 멈춰 있는 동안의 확인들: 즉시 실패, 새 스레드 없음
        start = time.time()
        for name in ("ab", "abc", "abcd"):
            assert (
                safety.safe_isfile(os.path.join(mountpoint, name), timeout=0.2)
                is False
            )
        # 기다리지 않고 곧바로 돌아왔는지만 본다. 세 번을 정말 두드렸다면
        # 제한 시간(0.2초)씩 0.6초가 걸린다 — 그 절반을 경계로 잡는다.
        # (0.1초로 잡았더니 머신이 바쁠 때 흔들렸다. "안 두드렸다"는 아래
        #  stat_calls 단언이 이미 못박고 있으므로 여기는 여유를 준다.)
        assert time.time() - start < 0.3
        assert len(stat_calls) == 1             # 더 두드리지 않았다

        # 멈췄던 스레드가 돌아오면 다시 실제로 확인한다
        time.sleep(0.9)
        assert safety.safe_isfile(os.path.join(mountpoint, "e"), timeout=0.2) is False
        assert len(stat_calls) == 2
    finally:
        time.sleep(0.9)                         # 남은 스레드가 조용히 끝나게
        safety.clear_cache()


def test_validate_skips_untouchable_paths(monkeypatch, guarded_root):
    """유효성 검사는 만지면 안 되는 자리를 stat 하지 않고 판정을 보류한다."""
    touched = []
    for name in ("isfile", "isdir", "exists"):
        real = getattr(os.path, name)

        def wrapper(path, _real=real):
            touched.append(str(path))
            return _real(path)

        monkeypatch.setattr(os.path, name, wrapper)

    dangerous = os.path.join(guarded_root, "j")
    valid, reason = validate_paths([dangerous], mode="open_file", must_exist=True)
    assert valid and reason == ""                 # 판정 보류 = 입력을 막지 않는다
    assert not [p for p in touched if p.startswith(dangerous)]

    # 한 단계 아래는 평소대로 실제 확인한다
    deep = os.path.join(guarded_root, "myaccount", "proj")
    valid, _reason = validate_paths([deep], mode="directory", must_exist=True)
    assert valid
    assert [p for p in touched if p.startswith(deep)]


def test_typing_guard_skips_dangerous_autochecks(qapp, guarded_root):
    """파일 이름 칸에 차단 경로 아래를 칠 때 자동 경로 확인을 건너뛴다."""
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QDialogButtonBox, QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog
    from custom_file_dialog.guard import _TypingGuard

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(os.path.dirname(guarded_root))
    dialog.show()
    _spin(qapp, 300)

    guards = [h for h in guard_dialog(dialog) if isinstance(h, _TypingGuard)]
    assert guards, "타이핑 가드가 걸려야 한다"
    guard = guards[0]

    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    edit.setFocus()
    QTest.keyClicks(edit, guarded_root + os.sep + "jX")
    qapp.processEvents()

    # "<차단>/j" 부터는 글자마다 건너뛴 기록이 남는다
    assert len(guard.skipped) >= 2, guard.skipped
    assert all(os.path.dirname(p) == guarded_root for p in guard.skipped)

    # 자동 판정 없이도 경로를 마저 쳐서 확정할 수 있게 버튼은 살아 있다
    box = dialog.findChild(QDialogButtonBox, "buttonBox")
    assert box.button(QDialogButtonBox.StandardButton.Open).isEnabled()
    dialog.done(0)


def test_typing_guard_keeps_normal_autochecks(qapp, guarded_root, tmp_path):
    """안전한 자리에서는 Qt 원래의 자동 확인(버튼 활성 판정)이 그대로 돈다."""
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QDialogButtonBox, QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog
    from custom_file_dialog.guard import _TypingGuard

    (tmp_path / "hello.txt").write_text("x")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setDirectory(str(tmp_path))
    dialog.show()
    _spin(qapp, 300)

    guards = [h for h in guard_dialog(dialog) if isinstance(h, _TypingGuard)]
    assert guards
    guard = guards[0]

    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    box = dialog.findChild(QDialogButtonBox, "buttonBox")
    button = box.button(QDialogButtonBox.StandardButton.Open)

    edit.setFocus()
    QTest.keyClicks(edit, "hello.txt")
    qapp.processEvents()
    assert button.isEnabled()                    # 있는 파일 -> 열기 가능

    edit.clear()
    QTest.keyClicks(edit, "nope.txt")
    qapp.processEvents()
    assert not button.isEnabled()                # 없는 파일 -> 원래처럼 비활성

    assert not guard.skipped                     # 안전한 자리는 건너뛴 것이 없다
    dialog.done(0)


_SYSCALL_REPRO = """
import os, sys, tempfile

# **QApplication 보다 먼저** 설정 저장 위치를 임시 폴더로 돌린다 (conftest 와
# 같은 이유·같은 방법). 이걸 빼면 이 자식 프로세스가 QFileDialog 상태를
# 사용자의 진짜 ~/.config/QtProject.conf 에 쓴다 — 거기 우리 분류 폴더(비-ASCII
# 이름) 경로가 한 번 들어가면 Qt5/Qt6 을 번갈아 도는 이 스위트에서 왕복마다
# 배로 늘어나, 실제로 그 파일이 805MB 가 됐고 그때부터 이 스크립트가
# QFileDialog.show() 에서 100% SIGSEGV 로 죽었다.
from qtpy.QtCore import QSettings
_settings_dir = tempfile.mkdtemp(prefix="cfd-syscall-settings-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _settings_dir)
    QSettings.setPath(_fmt, QSettings.Scope.SystemScope, _settings_dir)

from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QLineEdit
from custom_file_dialog import CustomFileDialog, safety

app = QApplication([])
root = tempfile.mkdtemp(prefix="cfd-syscall-")
guard = os.path.join(root, "user")
os.makedirs(os.path.join(guard, "myaccount"))
work = os.path.join(root, "work")
os.makedirs(work)

if os.environ.get("CFD_SAFETY") == "1":
    safety.configure(guarded_roots=[guard], min_depth=2)
else:
    safety.reset()
    safety.has_automounts = lambda: False   # 이 머신의 autofs 와 무관하게

dialog = CustomFileDialog(None, mode="open_file", directory=work)
dialog.show()
app.processEvents()
edit = dialog.findChild(QLineEdit, "fileNameEdit")
edit.setFocus()
QTest.keyClicks(edit, guard + "/zZ")        # "/user/z", "/user/zZ" 를 친다
for _ in range(20):
    app.processEvents()
dialog.done(0)
"""


@pytest.mark.skipif(shutil.which("strace") is None, reason="strace 필요")
def test_typing_touches_no_guarded_child_at_syscall_level(tmp_path):
    """시스템 콜 수준의 증명 — 타이핑한 미완성 경로를 정말 안 만지는지.

    automount 에서는 자식 이름의 access()/stat() 하나하나가 마운트 시도라,
    파이썬 쪽 기록이 아니라 **실제 syscall 이 없는 것**까지 확인해야 한다.
    가드를 끈 대조 실행으로 관측 자체가 되는지도 함께 확인한다(Qt 가 언젠가
    동작을 바꿔 관측이 안 되면 실패 대신 skip).
    """
    import subprocess

    import custom_file_dialog

    script = tmp_path / "repro.py"
    script.write_text(_SYSCALL_REPRO, encoding="utf-8")
    src = os.path.dirname(os.path.dirname(custom_file_dialog.__file__))

    def typed_child_touches(safety_on):
        trace = tmp_path / ("trace_%d.out" % safety_on)
        env = dict(
            os.environ,
            QT_QPA_PLATFORM="offscreen",
            PYTHONPATH=src,
            CFD_SAFETY="1" if safety_on else "0",
        )
        try:
            subprocess.run(
                ["strace", "-f", "-e", "trace=%file", "-o", str(trace),
                 sys.executable, str(script)],
                env=env, check=True, capture_output=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            # strace 아래의 Qt 시작은 머신이 바쁘면 몇 분씩 걸린다. 시간이
            # 모자란 것은 이 불변식에 대해 아무것도 말해 주지 않으므로,
            # 거짓 실패로 다른 회귀를 가리지 않게 건너뛴다.
            pytest.skip("strace 실행이 제한 시간 안에 안 끝났다 — 머신이 바쁘다")
        lines = trace.read_text(errors="replace").splitlines()
        return [line for line in lines if "user/z" in line]

    control = typed_child_touches(safety_on=False)
    if not control:
        pytest.skip("이 Qt 는 타이핑 중 경로를 만지지 않는다 — 관측 불가")

    assert typed_child_touches(safety_on=True) == []


def test_dialog_start_at_avoids_automount_parent(qapp, monkeypatch, tmp_path):
    """안 붙은 automount 하위를 시작 위치로 줘도 automount 뿌리를 열지 않는다.

    ``directory="/user/myaccount/f.csv"`` 인데 myaccount 가 아직 안 붙어 있으면
    safe_isdir 가 False 라 부모로 올라가는데, 그 부모(``/user``)를 열면
    나열 = 전부 마운트다. 그때는 시작 위치를 잡지 않는 편이 안전하다.
    """
    from custom_file_dialog import CustomFileDialog, safety

    root = tmp_path / "user"
    root.mkdir()

    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (str(root), "autofs", "auto.user"),
        ],
    )
    try:
        dialog = CustomFileDialog(
            None, mode="save_file", directory=str(root / "myaccount" / "f.csv")
        )
        opened = os.path.normpath(dialog.directory().absolutePath())
        assert opened != os.path.normpath(str(root))       # automount 뿌리가 아니다
        assert not opened.startswith(str(root) + os.sep)   # 그 아래도 아니다
        dialog.deleteLater()
    finally:
        safety.clear_cache()


def test_may_open_blocks_shallow_paths(shallow_tree):
    """확정 판정 — 깊이 ≤ min_depth 는 막고, 더 깊으면 automount 위라도 허용."""
    from custom_file_dialog import safety

    root, depth = shallow_tree
    safety.configure(min_depth=depth + 1)
    assert not safety.may_open(root)                              # 깊이 < limit
    assert not safety.may_open(os.path.join(root, "j"))           # 깊이 == limit
    assert not safety.may_open(os.path.join(root, "myaccount"))       # 이름이 맞아도
    assert safety.may_open(os.path.join(root, "myaccount", "proj"))   # 깊이 > limit


def test_may_open_trailing_separator_is_explicit(shallow_tree):
    """끝의 구분자('/')는 "이 폴더를 열겠다"는 명시적 표기 — limit 깊이부터 허용.

    "/user/myaccount/" 는 열리고 "/user/myaccount" 는 막힌다. 더 얕은 "/user/" 는
    구분자를 붙여도 막힌다.
    """
    from custom_file_dialog import safety

    root, depth = shallow_tree
    safety.configure(min_depth=depth + 1)
    assert safety.may_open(os.path.join(root, "myaccount") + os.sep)  # 깊이 == limit + '/'
    assert safety.may_open(os.path.join(root, "j") + os.sep)      # 오타여도 명시면 허용
    assert not safety.may_open(root + os.sep)                     # 깊이 < limit 는 불가
    # 차단 경로는 구분자를 붙여도 막힌다
    safety.configure(guarded_roots=[root], min_depth=0)
    assert not safety.may_open(root + os.sep)


def test_may_open_blocks_guarded_roots(guarded_root):
    """차단 경로 자체는 물론, **그 바로 아래도** 구분자 없이는 확정 불가.

    확정도 그 이름 하나를 stat 하는 일이라 부모가 위험하면 막아야 한다 —
    ``/user`` 안에서 ``myaccount`` 를 찾는 것이 곧 마운트 시도다. 폴더로
    가려면 끝에 구분자를 붙여 "열겠다"고 밝힌다.
    """
    from custom_file_dialog import safety

    inner = os.path.join(guarded_root, "myaccount")
    assert not safety.may_open(guarded_root)            # 지목한 자리 자체
    assert not safety.may_open(guarded_root + os.sep)   # 구분자를 붙여도 안 된다
    assert not safety.may_open(inner)                   # 부모가 위험한 자리다
    assert safety.may_open(inner + os.sep)              # 명시하면 열린다
    assert safety.may_open(os.path.join(inner, "a.csv"))    # 부모가 안전한 자리


def test_min_depth_blocks_enter_on_shallow_path(qapp, shallow_tree):
    """min_depth 만 켜도 얕은 경로는 Enter/열기 버튼으로 확정할 수 없다.

    타이핑 자동 확인을 다 막아도, Enter 의 확정은 Qt 가 GUI 스레드에서 입력
    경로를 stat 한다 — automount 에서는 그 한 번으로 멈춘다("/user/my" Enter).
    """
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker

    root, depth = shallow_tree
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(os.path.dirname(root))
    dialog.show()
    _spin(qapp, 300)

    installed = guard_dialog(dialog)
    blocker = [h for h in installed if isinstance(h, _AcceptBlocker)][0]
    edit = dialog.findChild(QLineEdit, "fileNameEdit")

    def press_enter():
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        return blocker.eventFilter(edit, event)

    edit.setText(os.path.join(root, "j"))            # 깊이 == min_depth
    assert press_enter() is True
    edit.setText(os.path.join(root, "myaccount"))        # 이름이 맞아도 같은 깊이
    assert press_enter() is True

    # 막히면 왜 막혔는지 안내한다 (비모달 — 테스트가 갇히지 않는다)
    assert blocker.notice is not None and blocker.notice.isVisible()
    assert "%d단계" % (depth + 1) in blocker.notice.text()
    assert os.sep in blocker.notice.text()

    # 끝에 구분자를 붙인 명시적 폴더 표기는 같은 깊이라도 열린다
    edit.setText(os.path.join(root, "myaccount") + os.sep)
    assert press_enter() is False

    edit.setText(os.path.join(root, "myaccount", "proj"))    # 더 깊으면 확정 가능
    assert press_enter() is False

    assert blocker.blocked
    dialog.close()


def test_typing_guard_python_route(qapp, guarded_root, tmp_path):
    """Qt6 폴백(내부 슬롯이 없을 때)의 버튼 활성 판정 — 어느 바인딩에서든 검증.

    Qt6 는 _q_updateOkButton 등이 메타오브젝트에서 사라져 재호출이 안 되므로
    같은 판정을 직접 한다. 그 경로를 강제로 태워 규칙(있는 파일만 열기 가능 ·
    저장은 이름만 · 위험한 자리는 건너뛰되 버튼 유지)을 잠근다.
    """
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QDialogButtonBox, QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog
    from custom_file_dialog.guard import _TypingGuard

    (tmp_path / "hello.txt").write_text("x")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setDirectory(str(tmp_path))
    dialog.show()
    _spin(qapp, 300)

    guards = [h for h in guard_dialog(dialog) if isinstance(h, _TypingGuard)]
    assert guards
    guard = guards[0]
    guard._route = "python"                  # Qt6 상황을 강제

    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    box = dialog.findChild(QDialogButtonBox, "buttonBox")
    button = box.button(QDialogButtonBox.StandardButton.Open)

    edit.setFocus()
    QTest.keyClicks(edit, "hello.txt")
    qapp.processEvents()
    assert button.isEnabled()                # 있는 파일 -> 열기 가능

    edit.clear()
    QTest.keyClicks(edit, "nope.txt")
    qapp.processEvents()
    assert not button.isEnabled()            # 없는 파일 -> 비활성

    # 위험한 자리(차단 하위)는 stat 없이 건너뛰고 버튼은 살아 있다
    edit.clear()
    QTest.keyClicks(edit, guarded_root + os.sep + "jX")
    qapp.processEvents()
    assert guard.skipped
    assert button.isEnabled()
    dialog.done(0)


def test_consecutive_enter_navigation(qapp, shallow_tree):
    """얕은 폴더로 연속해서 Enter 이동이 된다 (한 번 들어간 뒤에도).

    자동 확인을 건너뛸 때 목록 선택을 그대로 두면, 직전에 들어간 폴더의 선택이
    남아 selectedFiles() 가 **입력한 경로 대신 그 선택**을 돌려준다. 그래서
    "/user/abcd/ 로 들어간 뒤 /user/abcdddd/ 를 쳐도 안 들어가지는" 버그가 났다.
    """
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety

    root, depth = shallow_tree
    for name in ("abcd", "abcdddd"):
        os.makedirs(os.path.join(root, name), exist_ok=True)
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setDirectory(os.path.dirname(root))
    dialog.show()
    _spin(qapp, 300)
    guard_dialog(dialog)
    edit = dialog.findChild(QLineEdit, "fileNameEdit")

    def go(name):
        edit.clear()
        _spin(qapp, 50)
        edit.setFocus()
        QTest.keyClicks(edit, os.path.join(root, name) + os.sep)
        _spin(qapp, 100)
        QTest.keyClick(edit, Qt.Key.Key_Return)
        _spin(qapp, 300)
        return os.path.basename(dialog.directory().absolutePath())

    assert go("abcd") == "abcd"
    assert go("abcdddd") == "abcdddd"        # 다른 계정으로도 이동된다
    assert go("abcd") == "abcd"              # 다시 돌아오는 것도 된다
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_typing_guard_keeps_matching_selection(qapp, shallow_tree):
    """텍스트와 **일치하는** 선택은 남기고, 어긋나는 묵은 선택만 지운다.

    목록에서 클릭해 고르면 Qt 가 파일 이름 칸을 그 이름으로 채우므로 둘이
    일치한다 — 그 선택까지 지우면 클릭 선택이 즉시 풀려 버린다.
    """
    from qtpy.QtCore import QItemSelectionModel
    from qtpy.QtWidgets import QFileDialog, QLineEdit, QListView

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _TypingGuard

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    for name in ("고른파일.csv", "다른파일.csv"):
        with open(os.path.join(inner, name), "w", encoding="utf-8") as handle:
            handle.write("x")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setDirectory(inner)
    dialog.show()
    _spin(qapp, 500)
    guard = [h for h in guard_dialog(dialog) if isinstance(h, _TypingGuard)][0]

    view = dialog.findChild(QListView, "listView")
    model, root_index = view.model(), view.rootIndex()
    rows = {
        model.index(r, 0, root_index).data(): model.index(r, 0, root_index)
        for r in range(model.rowCount(root_index))
    }
    assert "고른파일.csv" in rows, sorted(rows)

    def select(name):
        view.selectionModel().select(
            rows[name], QItemSelectionModel.SelectionFlag.ClearAndSelect
        )

    def selected():
        return sorted(
            index.data()
            for index in view.selectionModel().selectedIndexes()
            if index.column() == 0
        )

    # 텍스트와 일치하는 선택 -> 남는다
    select("고른파일.csv")
    guard._sync_selection("고른파일.csv")
    assert selected() == ["고른파일.csv"]

    # 다른 경로를 치면 묵은 선택은 지워진다(그래야 그 경로로 확정된다)
    guard._sync_selection(os.path.join(root, "abcd") + os.sep)
    assert selected() == []

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_guard_dialog_is_idempotent(qapp, guarded_root):
    """이미 걸린 다이얼로그에 다시 걸어도 장치가 겹치지 않는다.

    두 번 걸리면 Enter 한 번에 안내 팝업이 두 번 뜨고, 뒤에 걸린 타이핑 가드가
    앞엣것의 연결을 끊어 고아를 남긴다.
    """
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import guard_dialog
    from custom_file_dialog.guard import _AcceptBlocker, _TypingGuard

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(os.path.dirname(guarded_root))

    first = guard_dialog(dialog)
    assert first                                   # 처음에는 걸린다
    assert guard_dialog(dialog) == []              # 두 번째는 아무 일도 안 한다

    assert len(dialog.findChildren(_TypingGuard)) == 1
    assert len(dialog.findChildren(_AcceptBlocker)) == 1
    dialog.close()


def test_safety_forces_qt_dialog_over_native(qapp, guarded_root, monkeypatch):
    """안전장치가 켜져 있으면 네이티브 창 대신 Qt 자체 창으로 연다.

    네이티브 창은 OS 가 그려서 자동완성도 확정도 가로챌 수 없다. 예전에는
    꾸밀 것(즐겨찾기 등)이 없으면 그대로 네이티브로 열려, guarded_roots 와
    min_depth 를 켜 두고도 보호가 통째로 빠졌다.
    """
    from custom_file_dialog import exec_file_dialog, safety

    used = []
    monkeypatch.setattr(
        dialog_module, "_run_dialog",
        lambda *a, **k: (used.append("native"), ([], ""))[1],
    )
    monkeypatch.setattr(
        dialog_module, "exec_dialog",
        lambda dialog: (used.append(type(dialog).__name__), 0)[1],
    )

    # 안전장치가 켜진 상태(guarded_root 픽스처) -> 인스턴스 다이얼로그
    exec_file_dialog(mode="open_file", native=True)
    assert used == ["CustomFileDialog"], used

    # 아무 설정도 없고 autofs 도 없으면 예전처럼 네이티브를 쓴다
    used.clear()
    safety.reset()
    monkeypatch.setattr(safety_mounts, "has_automounts", lambda: False)
    exec_file_dialog(mode="open_file", native=True)
    assert used == ["native"], used


def test_min_depth_blocks_entering_shallow_places(qapp, shallow_tree):
    """min_depth 만 켜도 얕은 자리로 **들어가는** 통로가 막힌다.

    들어가는 순간 그 자리가 통째로 나열된다 — automount 라면 그 한 번으로
    전부 마운트다. 예전에는 상위 폴더(↑)·더블클릭·"Look in" 이 모두 열려 있어
    /user/myaccount 에서 ↑ 한 번이면 /user 가 나열됐다.
    """
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QFileDialog, QToolButton

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _ParentBlocker

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(inner)
    dialog.show()
    _spin(qapp, 300)

    installed = guard_dialog(dialog)
    blocker = [h for h in installed if isinstance(h, _ParentBlocker)][0]
    button = dialog.findChild(QToolButton, "toParentButton")

    def click_up():
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return blocker.eventFilter(button, event)

    # /user/myaccount 에서 ↑ -> /user(얕음) 이므로 삼킨다
    assert click_up() is True
    assert blocker.blocked == [os.path.normpath(root)]

    # 충분히 깊은 자리에서는 평소대로 올라간다
    deeper = os.path.join(inner, "proj")
    dialog.setDirectory(deeper)
    assert click_up() is False
    dialog.close()


def test_min_depth_bounces_back_from_shallow_place(qapp, shallow_tree):
    """그래도 들어가졌으면 직전 폴더로 되돌린다(마지막 방어).

    이미 읽은 뒤라 늦지만, 그 자리에 머무르며 계속 갱신되지는 않게 한다.
    """
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import guard_dialog, safety

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(inner)
    assert "bounce" in guard_dialog(dialog)

    dialog.setDirectory(root)                 # 어떻게든 얕은 자리로 갔다면
    dialog.directoryEntered.emit(root)        # 사용자 이동과 같은 신호
    # 되돌리기는 **한 틱 미룬다.** 신호 처리 도중에 setDirectory 를 부르면
    # Qt6 이 사이드바 이동 한가운데서 재진입해 세그폴트한다(PyQt6·PySide6
    # 에서 100% 재현). 그 자리에서 되돌아왔다면 미루기가 사라진 것이다.
    assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(root)
    _spin(qapp, 50)
    assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(inner)
    dialog.close()


def test_min_depth_keeps_normal_browsing(qapp, shallow_tree):
    """이동 차단이 **평범한 탐색까지 막지는 않는다**.

    얕은 자리만 막아야 한다 — 충분히 깊은 폴더로 들어가기, 그 안의 파일을
    더블클릭해 고르기, 한 단계 위로 올라가기는 그대로 돼야 한다.
    """
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QFileDialog, QTreeView

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _ItemBlocker

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")          # 깊이 == min_depth
    with open(os.path.join(inner, "설계도.csv"), "w", encoding="utf-8") as handle:
        handle.write("x")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(inner)
    dialog.show()
    _spin(qapp, 500)

    installed = guard_dialog(dialog)
    tree = dialog.findChild(QTreeView, "treeView")
    blocker = [
        h for h in installed if isinstance(h, _ItemBlocker) and h._view is tree
    ][0]

    model, root_index = tree.model(), tree.rootIndex()
    rows = {
        model.index(r, 0, root_index).data(): model.index(r, 0, root_index)
        for r in range(model.rowCount(root_index))
    }
    assert {"proj", "설계도.csv"} <= set(rows), sorted(rows)

    def double_click(index):
        tree.scrollTo(index)
        point = tree.visualRect(index).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(point),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return blocker.eventFilter(tree.viewport(), event)

    # 깊은 자리의 하위 폴더와 파일은 그대로 열린다
    assert double_click(rows["proj"]) is False
    assert double_click(rows["설계도.csv"]) is False
    assert blocker.blocked == []

    # 그 안에서 파일 이름을 쳐서 확정하는 것도 된다(깊이 > min_depth)
    assert safety.may_open(os.path.join(inner, "설계도.csv"))
    dialog.close()


def test_mountinfo_survives_non_utf8_paths(monkeypatch, tmp_path):
    """마운트 경로에 비UTF-8 바이트가 있어도 판정이 터지지 않는다.

    순정 utf-8 로 읽으면 UnicodeDecodeError 가 나는데 그것은 OSError 가 아니라,
    mount_for -> may_stat/may_enter/safe_* 가 전부 예외로 터졌다(= 다이얼로그
    생성과 키 입력마다 크래시). 옛 공유의 EUC-KR 이름 등이 실제로 그렇다.
    """
    from custom_file_dialog import safety

    fake = tmp_path / "mountinfo"
    fake.write_bytes(
        b"36 25 0:32 / /mnt/\xc7\xd1\xb1\xdb rw - cifs //srv/\xc7\xd1\xb1\xdb rw\n"
        b"37 25 0:33 / /mnt/ok rw - nfs4 srv:/e rw\n"
    )
    monkeypatch.setattr(safety_mounts, "MOUNTINFO", str(fake))
    safety.reset()                                   # 앞 테스트 설정에 기대지 않는다
    safety.clear_cache()
    try:
        mounts = safety.iter_mounts(refresh=True)
        assert len(mounts) == 2                      # 두 줄 다 살아 있다
        assert safety.mount_for("/mnt/ok/a")[1] == "nfs4"

        # 깨진 이름이 섞인 표에서도 판정이 **제 값을 낸다**(예외가 아니라)
        assert safety.may_stat("/mnt/ok/a/b/c") is True
        assert safety.may_enter("/mnt/ok/a") is True
        assert safety.protection_active() is False   # autofs 도 설정도 없다

        # 그 깨진 이름의 마운트도 정상으로 잡힌다
        broken = mounts[0][0]
        assert safety.mount_for(os.path.join(broken, "x"))[1] == "cifs"

        # autofs 로 잡히면 보호가 켜지는 것도 확인(같은 표에서)
        fake.write_bytes(
            b"36 25 0:32 / /mnt/\xc7\xd1\xb1\xdb rw - autofs auto.x rw\n"
        )
        safety.clear_cache()
        assert safety.protection_active() is True
        assert safety.may_enter(mounts[0][0]) is False
    finally:
        safety.clear_cache()
        safety.reset()


def test_start_dir_respects_guarded_root(qapp, guarded_root):
    """시작 폴더가 차단 경로면 그 자리에서 열지 않는다.

    setDirectory 는 directoryEntered 를 내지 않아 마지막 방어가 안 걸리므로
    여기서 걸러야 한다. path_timeout=None 으로 시간 확인을 꺼도 마찬가지다
    (예전에는 그 경우 순정 os.path.isdir 를 써서 그대로 열렸다).
    """
    from custom_file_dialog import CustomFileDialog

    dialog = CustomFileDialog(
        None, mode="open_file", directory=guarded_root, path_timeout=None
    )
    opened = os.path.normpath(dialog.directory().absolutePath())
    assert opened != os.path.normpath(guarded_root)
    dialog.deleteLater()
    _spin(qapp, 50)         # 지연 삭제를 여기서 소화한다


def test_start_dir_respects_min_depth(qapp, shallow_tree):
    """min_depth 로만 막아 둔 얕은 자리도 시작 폴더로 열리지 않는다."""
    from custom_file_dialog import CustomFileDialog, safety

    root, depth = shallow_tree
    safety.configure(min_depth=depth + 1)

    dialog = CustomFileDialog(None, mode="open_file", directory=root)
    opened = os.path.normpath(dialog.directory().absolutePath())
    assert opened != os.path.normpath(root)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_blocked_buttons_do_not_stay_pressed(qapp, shallow_tree):
    """차단으로 릴리즈를 삼켜도 버튼이 눌린 채로 남지 않는다."""
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QDialogButtonBox, QFileDialog, QLineEdit, QToolButton

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker, _ParentBlocker

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(inner)
    dialog.show()
    _spin(qapp, 300)
    installed = guard_dialog(dialog)

    def release(widget, blocker):
        widget.setDown(True)
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return blocker.eventFilter(widget, event)

    up = dialog.findChild(QToolButton, "toParentButton")
    parent_blocker = [h for h in installed if isinstance(h, _ParentBlocker)][0]
    assert release(up, parent_blocker) is True
    assert not up.isDown()                       # 눌린 상태로 남지 않는다

    accept_blocker = [h for h in installed if isinstance(h, _AcceptBlocker)][0]
    box = dialog.findChild(QDialogButtonBox, "buttonBox")
    open_button = box.button(QDialogButtonBox.StandardButton.Open)
    dialog.findChild(QLineEdit, "fileNameEdit").setText(inner)   # 얕은 경로
    assert release(open_button, accept_blocker) is True
    assert not open_button.isDown()
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_resolve_start_dir_skips_missing_parent(tmp_path):
    """없는 폴더는 시작 위치로 쓰지 않고 다음 후보로 넘어간다.

    입력창에 오타 경로가 남아 있으면 start_dir 을 무시하고 존재하지 않는
    폴더에서 열렸다.
    """
    alive = str(tmp_path / "정상")
    os.mkdir(alive)
    missing = str(tmp_path / "없는폴더" / "x.csv")

    resolved = dialog_module.resolve_start_dir(
        [missing], start_dir=alive, last_dir=None, mode=SelectMode.OPEN_FILE
    )
    assert resolved == alive

    # 폴더 모드도 같다
    resolved = dialog_module.resolve_start_dir(
        [missing], start_dir=alive, mode=SelectMode.DIRECTORY
    )
    assert resolved == alive


# ---------------------------------------------------------------------------
# 판정들 사이의 불변식 — 통로가 늘어나도 규칙이 어긋나지 않게 잠근다
# ---------------------------------------------------------------------------

_SETUPS = [
    dict(),
    dict(min_depth=2),
    dict(guarded_roots=["/user"]),
    dict(guarded_roots=["/user"], min_depth=2),
    dict(allow_listing=False),
    dict(guarded_roots=["/user"], min_depth=3, allow_listing=False),
]

_PATHS = [
    "/", "/user", "/user/my", "/user/myaccount", "/user/myaccount/proj",
    "/user/myaccount/proj/a.csv", "/home", "/home/me", "/home/me/x.csv", "/tmp/a",
]


@pytest.mark.parametrize("automount", [False, True], ids=["autofs없음", "autofs있음"])
@pytest.mark.parametrize("setup", _SETUPS, ids=lambda s: str(sorted(s)) or "기본")
def test_decision_invariants(setup, automount, monkeypatch):
    """설정 × 경로를 모두 돌며 판정끼리 지켜야 할 관계를 확인한다.

    이 프로젝트의 버그는 대부분 **통로를 하나 더 막을 때 나머지와 규칙이
    어긋나서** 났다(확정만 autofs 를 안 보던 것 등). 개별 시나리오 테스트는
    새로 생긴 통로를 알지 못하므로, 관계 자체를 잠근다.
    """
    from custom_file_dialog import safety

    if automount:
        # 이 머신에는 autofs 가 없다. 그래서 "설정 없이 autofs 만 있는" 조합이
        # 검증에서 통째로 빠졌고, 실제로 그 자리에 구멍이 있었다
        # (끝에 / 를 붙이면 autofs 뿌리가 열렸다).
        monkeypatch.setattr(
            safety_mounts, "iter_mounts",
            lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                                   ("/user", "autofs", "auto.user")],
        )
    safety.reset()
    safety.clear_cache()
    safety.configure(**setup)
    try:
        for path in _PATHS:
            guarded = safety.is_guarded(path)

            # 나열이 되면 진입도 된다(allow_listing 만 더 엄격하다)
            if safety.may_list(path):
                assert safety.may_enter(path), path

            # 통째로 읽으면 위험한 자리는 진입할 수 없다
            if safety.risky_place(path):
                assert not safety.may_enter(path), path

            if guarded:
                # 지목한 자리는 어떤 표기로도 열리지 않는다
                assert not safety.may_enter(path), path
                assert not safety.may_open(path), path
                assert not safety.may_open(path + "/"), path
                continue

            # 구분자 없는 확정은 "이름 하나를 만지는" 일이라 자동 stat 과 같은 판정
            assert safety.may_open(path) == safety.may_stat(path), path

            # 끝에 구분자를 붙인 명시적 표기는 깊이만 본다 — 단 automount
            # **지점 자체**는 열 수 없다(그 자리를 여는 것은 아래를 전부
            # 마운트해 보라는 뜻이라, 하나만 붙이는 하위와 위험이 다르다).
            limit = safety.min_depth()
            expected = (limit <= 0 or safety.path_depth(path) >= limit) and not (
                safety.is_automount_point(path)
            )
            assert safety.may_open(path + "/") == expected, path

            # 어떤 표기로도 automount 지점 자체는 열리지 않는다
            if safety.is_automount_point(path):
                assert not safety.may_open(path), path
                assert not safety.may_open(path + "/"), path
                assert not safety.may_enter(path), path
    finally:
        safety.clear_cache()
        safety.reset()


def test_every_guard_uses_a_shared_decision():
    """다이얼로그에 거는 장치들이 **공용 판정**을 쓰는지 확인한다.

    장치가 제 나름의 조건을 들고 있으면(예전 may_open 이 그랬다) 규칙이
    갈라진다. 새 장치를 넣을 때 판정 없이 직접 조건을 쓰면 여기서 걸린다.
    """
    import inspect

    from custom_file_dialog import guard

    source = inspect.getsource(guard)
    decisions = ("may_enter", "may_open", "may_stat", "may_list")
    assert all(name in source for name in decisions), "판정을 안 쓰는 장치가 있다"

    # 무엇을 걸지·무엇을 막을지 **판단**할 때 설정을 직접 조합하면 안 된다.
    # 규칙이 두 곳으로 갈라져 한쪽만 고치는 실수가 난다(실제로 그렇게 났다).
    for raw in ("guarded_roots()", "path_depth(", "is_too_shallow("):
        assert raw not in source, "guard 가 설정을 직접 본다: %s" % raw

    # min_depth() 는 **안내 문구를 고를 때만** 쓴다 — 무엇을 막을지 판단하는
    # 데 쓰면 규칙이 갈라진다. 그래서 문구를 고르는 함수 안에만 있어야 한다.
    notice = inspect.getsource(guard._block_message)
    assert source.count("min_depth()") == notice.count("min_depth()") > 0

    # listing_allowed() 도 한 군데뿐이다 — 자동완성 모델의 **뿌리**(경로가 없어
    # may_list 로 물을 수 없는 자리)를 처리할 때만 쓴다.
    assert source.count("listing_allowed()") == 1
    assert "listing_allowed()" in inspect.getsource(guard.GuardedFileSystemModel)


def test_automount_root_never_opens_even_with_separator(monkeypatch, tmp_path):
    """automount **지점 자체**는 끝에 구분자를 붙여도 열 수 없다.

    설정 없이 autofs 만 있는 시스템에서 min_depth 가 0 이라, 명시적 표기
    예외가 "깊이 조건 없음 = 무조건 허용"으로 새어 ``/user/`` 를 Enter 로 열 수
    있었다. 그 자리를 여는 것은 아래 이름을 전부 마운트해 보라는 뜻이다.
    """
    from custom_file_dialog import safety

    root = tmp_path / "user"
    (root / "myaccount").mkdir(parents=True)
    safety.reset()
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (str(root), "autofs", "auto.user")],
    )
    try:
        assert safety.is_automount_point(str(root))
        assert not safety.may_open(str(root))            # 지점 자체
        assert not safety.may_open(str(root) + os.sep)   # 구분자를 붙여도
        assert not safety.may_enter(str(root))

        # 그 아래의 특정 이름은 명시하면 열린다(하나만 마운트하는 일이다)
        inner = os.path.join(str(root), "myaccount")
        assert safety.may_open(inner + os.sep)
        assert not safety.may_open(inner)                # 구분자 없이는 안 된다
    finally:
        safety.clear_cache()
        safety.reset()


def test_already_mounted_automount_is_usable(monkeypatch, tmp_path):
    """이미 붙은 automount 지점은 평소대로 쓸 수 있어야 한다.

    systemd automount 는 트리거된 뒤에도 autofs 줄이 mountinfo 에 남고 그 위에
    실제 파일시스템이 겹쳐 올라온다. 먼저 나온 줄(autofs)을 실효 마운트로 보면
    **잘 도는 폴더를 영영 열 수 없다** — autofs /home 이나 systemd 로 붙인
    /mnt/* 를 쓰는 머신에서 다이얼로그가 무용지물이 된다.
    """
    from custom_file_dialog import safety

    safety.reset()
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            ("/mnt/data", "autofs", "systemd-1"),
            ("/mnt/data", "nfs4", "srv:/data"),      # 이미 붙었다(위에 겹친 줄)
            ("/user", "autofs", "auto.user"),        # 아직 안 붙었다
        ],
    )
    try:
        # 이미 붙은 자리 — 지점도 하위도 평소대로
        assert not safety.is_automount_point("/mnt/data")
        assert safety.may_enter("/mnt/data")
        assert safety.may_enter("/mnt/data/proj")
        assert safety.may_open("/mnt/data/")
        assert safety.mount_for("/mnt/data")[1] == "nfs4"

        # 아직 안 붙은 자리 — 그대로 막힌다
        assert safety.is_automount_point("/user")
        assert not safety.may_enter("/user")
        assert not safety.may_open("/user/")
    finally:
        safety.clear_cache()
        safety.reset()


def test_automount_point_block_is_explained(qapp, monkeypatch, tmp_path):
    """automount 지점을 막을 때도 왜 막혔는지 알려 준다.

    설정 없이 autofs 만 있으면 min_depth 가 0 이라 안내가 통째로 빠져, Enter 를
    쳐도 아무 반응이 없었다(키가 안 먹는 것으로 읽힌다).
    """
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker

    root = tmp_path / "user"
    root.mkdir()
    safety.reset()
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (str(root), "autofs", "auto.user")],
    )
    try:
        dialog = QFileDialog()
        dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
        dialog.setDirectory(str(tmp_path))
        dialog.show()
        _spin(qapp, 200)
        blocker = [h for h in guard_dialog(dialog) if isinstance(h, _AcceptBlocker)][0]

        edit = dialog.findChild(QLineEdit, "fileNameEdit")
        edit.setText(str(root) + os.sep)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        assert blocker.eventFilter(edit, event) is True     # 막고
        assert blocker.notice is not None                   # 이유를 알려 준다
        assert "자동 마운트" in blocker.notice.text()
        dialog.done(0)
        dialog.deleteLater()
        _spin(qapp, 50)
    finally:
        safety.clear_cache()
        safety.reset()


# automount 는 마운트 표가 여러 형태를 취한다. 이 계열 버그가 반복된 이유가
# 그중 일부만 보고 판단했기 때문이라, 형태별 기대를 표로 못 박는다.
_AUTOMOUNT_SHAPES = [
    # (이름, 마운트 표, 지점, 이미 붙은 하위 이름 또는 None)
    (
        "아직 안 붙음",
        [("/user", "autofs", "auto.user")],
        "/user",
        None,
    ),
    (
        "지점 자체가 붙음",
        [("/user", "autofs", "auto.user"), ("/user", "nfs4", "srv:/export")],
        "/user",
        None,
    ),
    (
        "하위가 붙음(실제 홈 패턴)",
        [("/user", "autofs", "auto.user"), ("/user/me", "nfs4", "srv:/home/me")],
        "/user",
        "me",
    ),
    (
        "중첩 automount",
        [("/net", "autofs", "-hosts"), ("/net/box", "nfs4", "box:/")],
        "/net",
        "box",
    ),
]


@pytest.mark.parametrize(
    "label,table,point,mounted", _AUTOMOUNT_SHAPES, ids=lambda v: v if isinstance(v, str) else ""
)
def test_automount_shapes(label, table, point, mounted, monkeypatch):
    """마운트 표의 형태마다 판정이 제 값을 내는지 표로 확인한다."""
    from custom_file_dialog import safety

    safety.reset()
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1")] + table,
    )
    try:
        point_is_live = any(
            p == point and fstype not in safety.AUTOMOUNT_FSTYPES
            for p, fstype, _src in table
        )

        unknown = point + "/whoever"        # 아직 붙지 않은(있을지도 모르는) 이름

        if point_is_live:
            # 지점 위에 실제 파일시스템이 겹쳐 올라왔다 = 이미 붙었다.
            # 그 자리도 그 아래도 평소대로 쓴다.
            assert safety.may_enter(point), label
            assert safety.may_open(point + "/"), label
            assert safety.may_stat(unknown), label
        else:
            # 지점 자체는 열 수 없다 — 여는 것이 곧 "아래를 전부 마운트해 보라"다
            assert not safety.may_enter(point), label
            assert not safety.may_open(point + "/"), label
            # 안 붙은 이름은 자동으로 만지지 않는다. 열려면 끝에 구분자를 붙여
            # "이 폴더를 열겠다"고 밝혀야 한다(그때 하나만 마운트된다).
            assert not safety.may_stat(unknown), label
            assert not safety.may_open(unknown), label
            assert safety.may_open(unknown + "/"), label

        if mounted:
            # 이미 붙은 하위는 자유롭게 쓴다(그 자리는 autofs 가 아니다)
            live = "%s/%s" % (point, mounted)
            assert safety.may_enter(live), label
            assert safety.may_open(live + "/"), label
            assert safety.may_open(live + "/a.csv"), label      # 그 안의 파일도
            assert safety.may_stat(live + "/a.csv"), label      # 자동 확인까지
    finally:
        safety.clear_cache()
        safety.reset()


def test_blocked_paths_always_explain_and_advice_works(qapp, monkeypatch, tmp_path):
    """막을 때는 **언제나** 이유를 알려 주고, 알려 준 대로 하면 실제로 열린다.

    예전에는 (1) autofs 만 있고 설정이 없으면 안내가 통째로 빠져 Enter 가
    먹지 않는 것처럼 보였고, (2) 안내의 예시가 문자열 조립 실수로 열 수 없는
    경로를 알려 주기도 했다. 열기 버튼은 활성 상태라 사용자는 이유를 못 듣고
    눌러도 아무 일이 없었다.
    """
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker

    root = tmp_path / "user"
    (root / "me").mkdir(parents=True)
    work = tmp_path / "작업"
    work.mkdir()

    safety.reset()                      # 설정 없음 = min_depth 0 (그 조합이 문제였다)
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (str(root), "autofs", "auto.user")],
    )
    try:
        dialog = QFileDialog()
        dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
        dialog.setDirectory(str(work))
        dialog.show()
        _spin(qapp, 200)
        blocker = [h for h in guard_dialog(dialog) if isinstance(h, _AcceptBlocker)][0]
        edit = dialog.findChild(QLineEdit, "fileNameEdit")

        def press(text):
            edit.setText(text)
            event = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
            )
            swallowed = blocker.eventFilter(edit, event)
            _spin(qapp, 50)
            return swallowed, (blocker.notice.text() if blocker.notice else "")

        # 막히는 네 자리 모두 안내가 뜬다
        for text in (str(root), str(root) + os.sep,
                     os.path.join(str(root), "me"),
                     os.path.join(str(root), "me", "a.csv")):
            swallowed, body = press(text)
            assert swallowed is True, text
            assert blocker.notice.isVisible(), text
            assert body.strip(), text

            # 안내에 예시가 있으면 **그대로 하면 실제로 열려야** 한다
            for line in body.split("\n"):
                for token in line.replace("(예:", "예)").split("예)")[1:]:
                    example = token.strip().rstrip(").").strip()
                    if example and "이름" not in example:
                        assert safety.may_open(example), (text, example)

        dialog.done(0)
        dialog.deleteLater()
        _spin(qapp, 50)
    finally:
        safety.clear_cache()
        safety.reset()


def test_accept_guard_handles_quotes_and_multiple_paths(qapp, shallow_tree):
    """따옴표가 든 파일명과 여러 개 선택을 모두 제대로 판정한다.

    따옴표 쪼개기를 모드와 무관하게 적용하면, 리눅스에서 합법인 ``a"b`` 같은
    이름이 엉뚱한 경로로 바뀌고 ``"`` 로 **끝나는** 입력은 빈 목록이 되어
    확정 차단이 통째로 새어 나간다. 여러 개 모드에서는 반대로 첫 경로만 보면
    뒤에 섞인 절대 경로가 그대로 빠져나간다(accept 는 전부 stat 한다).
    """
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)

    def blocked_for(text, mode):
        dialog = QFileDialog()
        dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
        dialog.setFileMode(mode)
        dialog.setDirectory(inner)
        dialog.show()
        _spin(qapp, 100)
        blocker = [h for h in guard_dialog(dialog) if isinstance(h, _AcceptBlocker)][0]
        dialog.findChild(QLineEdit, "fileNameEdit").setText(text)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        result = blocker.eventFilter(
            dialog.findChild(QLineEdit, "fileNameEdit"), event
        )
        dialog.done(0)
        dialog.deleteLater()
        _spin(qapp, 50)
        return result

    single = QFileDialog.FileMode.ExistingFile
    multi = QFileDialog.FileMode.ExistingFiles

    # 단일 선택: 따옴표는 그냥 이름의 일부다 — 쪼개지 말아야 판정이 맞다
    assert blocked_for(os.path.join(root, 'a"b'), single) is True
    assert blocked_for(os.path.join(root, 'my"'), single) is True   # 빈 목록이 되면 샌다

    # 여러 개 선택: 하나라도 막을 자리면 막는다(첫 것만 보면 새어 나간다)
    good = '"설계도.csv"'
    bad = '"설계도.csv" "%s"' % os.path.join(root, "someone")
    assert blocked_for(good, multi) is False
    assert blocked_for(bad, multi) is True


def test_unknown_paths_do_not_pile_up_threads(monkeypatch, tmp_path):
    """마운트 표가 없는 곳에서도 멈춘 확인은 **볼륨당 하나**만 돈다.

    윈도우·macOS·/proc 없는 컨테이너에서는 모든 경로가 "알 수 없음"이라
    이 갈래만 탄다. 여기에 묶음 키를 안 주면 죽은 UNC 경로를 입력창에 치는
    동안 키 입력마다 멈춘 스레드가 하나씩 쌓인다 — 이 모듈이 약속한
    "마운트당 하나"가 이 갈래에서만 깨졌다.
    """
    import threading

    from custom_file_dialog import safety

    monkeypatch.setattr(safety_mounts, "iter_mounts", lambda refresh=False: [])
    monkeypatch.setattr(safety_mounts, "table_available", lambda: False)
    safety.clear_cache()

    release = threading.Event()

    def never_returns(path):
        release.wait(10)            # 죽은 마운트 흉내 — 시간 안에 안 돌아온다
        return True

    try:
        for index in range(8):
            assert safety.safe_call(
                never_returns, str(tmp_path / ("이름%d" % index)), timeout=0.05
            ) is False
        # 첫 확인만 스레드를 만들고, 나머지는 곧바로 실패로 판정한다
        assert safety_reach.pending_checks() == 1
    finally:
        release.set()
        safety.clear_cache()


def test_missing_path_is_reachable_without_a_mount_table(monkeypatch, tmp_path):
    """마운트 표가 없어도 "아직 없는 파일"이 도달 불가로 뒤집히지 않는다.

    ``os.stat`` 은 없는 경로에서 예외를 내고 :func:`call_with_timeout` 은 그것을
    "못 끝냈다"로 보므로, 저장 모드가 검사하는 새 파일 경로가 윈도우에서만
    거절됐다(같은 경로가 리눅스에서는 True).
    """
    from custom_file_dialog import safety

    monkeypatch.setattr(safety_mounts, "iter_mounts", lambda refresh=False: [])
    monkeypatch.setattr(safety_mounts, "table_available", lambda: False)
    safety.clear_cache()
    try:
        assert safety.is_reachable(str(tmp_path / "아직없는파일.csv")) is True
        assert safety.is_reachable(str(tmp_path)) is True
    finally:
        safety.clear_cache()


def test_guarded_root_accepts_bytes(tmp_path):
    """``bytes`` 로 준 차단 경로가 조용히 무시되지 않는다.

    ``str(b"/user")`` 는 ``"b'/user'"`` 라는 글자 그대로의 이름이 되어, 막아
    달라고 한 자리가 **보호되지 않은 채** 통과했다.
    """
    from custom_file_dialog import safety

    root = tmp_path / "user"
    root.mkdir()
    safety.configure(guarded_roots=[os.fsencode(str(root))])
    try:
        assert safety.guarded_roots() == [str(root)]
        assert safety.is_guarded(str(root))
        assert not safety.may_enter(str(root))
        # 물어보는 쪽이 bytes 여도 같은 답이어야 한다(경로 해석이 한곳이다)
        assert safety.is_guarded(os.fsencode(str(root)))
    finally:
        safety.reset()


def test_block_notice_names_the_path_that_blocked(qapp, shallow_tree):
    """여러 개 모드에서 안내 팝업이 **막은 경로**를 짚는다.

    첫 경로만 넘기면, 뒤에 섞인 경로 때문에 막혔는데도 멀쩡한 쪽을 기준으로
    "끝에 '/' 를 붙이세요" 라고 안내한다 — 따라 해도 열리지 않는다.
    """
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    dialog.setDirectory(inner)
    dialog.show()
    _spin(qapp, 100)

    blocker = [h for h in guard_dialog(dialog) if isinstance(h, _AcceptBlocker)][0]
    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    shallow = os.path.join(root, "someone")
    edit.setText('"설계도.csv" "%s"' % shallow)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
    )
    assert blocker.eventFilter(edit, event) is True
    assert blocker.blocked == [shallow]         # 멀쩡한 첫 경로가 아니라
    if blocker.notice is not None:
        assert "설계도.csv" not in blocker.notice.text()
        blocker.notice.hide()

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_qt6_button_check_respects_quotes_in_names(qapp, tmp_path):
    """Qt6 경로(파이썬 판정)도 따옴표를 **모드에 맞게** 다룬다.

    확정 차단(_AcceptBlocker)만 모드를 보고 형제 판정 두 곳이 그대로면, 같은
    입력을 두 장치가 다르게 읽는다 — 리눅스에서 합법인 ``a"b`` 를 단일 선택
    모드에 치면 열기 버튼이 엉뚱한 경로로 판정되고, ``"`` 로 끝나는 이름은
    경로 목록이 비어 버튼이 아예 죽는다.
    """
    from qtpy.QtWidgets import QDialogButtonBox, QFileDialog, QLineEdit

    from custom_file_dialog.guard import _TypingGuard

    for name in ('a"b', 'my"'):
        (tmp_path / name).write_text("x")

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setDirectory(str(tmp_path))
    dialog.show()
    _spin(qapp, 100)

    edit = dialog.findChild(QLineEdit, "fileNameEdit")
    guard = _TypingGuard(dialog, edit)
    box = dialog.findChild(QDialogButtonBox, "buttonBox")
    accept = box.button(QDialogButtonBox.StandardButton.Open)

    # 단일 선택 — 따옴표는 이름의 일부다. 있는 파일이므로 버튼이 살아 있어야 한다.
    for name in ('a"b', 'my"'):
        guard._update_accept_python(name)
        assert accept.isEnabled(), name

    # 없는 이름이면 그대로 죽는다(판정이 그냥 늘 True 인 것이 아님을 못박는다)
    guard._update_accept_python('없는"이름')
    assert not accept.isEnabled()

    # 여러 개 모드에서는 따옴표가 구분자다(이름에 든 따옴표는 쪼갤 수 없다)
    for name in ("하나.txt", "둘.txt"):
        (tmp_path / name).write_text("x")
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    guard._update_accept_python('"하나.txt" "둘.txt"')
    assert accept.isEnabled()
    guard._update_accept_python('"하나.txt" "없는것.txt"')
    assert not accept.isEnabled()

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_one_dead_share_does_not_lock_the_whole_filesystem(monkeypatch, tmp_path):
    """마운트 표가 없어도 **한 자리가 멈추면 그 자리만** 잠긴다.

    묶음 키를 파일시스템 뿌리 하나로 잡으면, 그런 시스템(macOS · /proc 없는
    컨테이너)에서는 모든 경로가 이 갈래로 오므로 죽은 공유 하나를 확인한
    순간 프로세스의 모든 경로 확인이 실패로 떨어진다 — 멀쩡한 로컬 파일이
    "존재하지 않습니다"가 되고 다이얼로그가 cwd 에서 열린다.
    """
    import threading

    from custom_file_dialog import safety

    monkeypatch.setattr(safety_mounts, "iter_mounts", lambda refresh=False: [])
    monkeypatch.setattr(safety_mounts, "table_available", lambda: False)
    safety.clear_cache()

    dead = tmp_path / "공유" / "죽은서버"
    dead.mkdir(parents=True)
    alive = tmp_path / "작업"
    alive.mkdir()
    (alive / "설계도.csv").write_text("x")

    release = threading.Event()

    def never_returns(path):
        release.wait(10)
        return True

    try:
        # 죽은 자리를 한 번 두드려 그 키를 눌러 둔다
        assert safety.safe_call(
            never_returns, str(dead / "a.csv"), timeout=0.05
        ) is False
        # 같은 자리는 스레드를 더 만들지 않고 곧바로 실패
        assert safety.safe_call(
            never_returns, str(dead / "b.csv"), timeout=0.05
        ) is False
        assert safety_reach.pending_checks() == 1

        # **무관한 자리**는 그대로 동작해야 한다
        assert safety.safe_exists(str(alive / "설계도.csv")) is True
        assert safety.safe_isdir(str(alive)) is True
        assert safety.is_reachable(str(alive / "설계도.csv")) is True
        assert validate_paths(
            [str(alive / "설계도.csv")], mode=SelectMode.OPEN_FILE, timeout=0.05
        )[0]
    finally:
        release.set()
        safety.clear_cache()


def test_trailing_separator_counts_per_typed_path(qapp, shallow_tree):
    """끝 구분자 판정을 **그 경로의 조각**으로 한다.

    입력창 원문 전체를 보면 여러 개 모드에서 원문이 따옴표로 끝나므로 늘
    "구분자를 안 붙였다"가 된다 — 사용자가 시킨 대로 했는데도 막히고,
    안내는 이미 한 일을 또 하라고 한다.
    """
    from qtpy.QtCore import QEvent, Qt
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QLineEdit

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _AcceptBlocker

    root, depth = shallow_tree
    # 이 깊이에서는 구분자가 판정을 가른다 — 붙이면 열리고 안 붙이면 막힌다
    target = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)
    assert safety.may_open(target) is False
    assert safety.may_open(target + os.sep) is True

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    dialog.setDirectory(os.path.join(target, "proj"))
    dialog.show()
    _spin(qapp, 100)

    blocker = [h for h in guard_dialog(dialog) if isinstance(h, _AcceptBlocker)][0]
    edit = dialog.findChild(QLineEdit, "fileNameEdit")

    def blocked_for(text):
        edit.setText(text)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        result = blocker.eventFilter(edit, event)
        if blocker.notice is not None:
            blocker.notice.hide()
        return result

    # 구분자를 붙였으면 여러 개 모드에서도 열린다 — 원문이 따옴표로 끝나는
    # 것과 무관하게, 그 **조각**이 구분자로 끝나는지를 봐야 한다
    assert blocked_for('"%s/"' % target) is False
    # 안 붙이면 그대로 막힌다
    assert blocked_for('"%s"' % target) is True
    # 단일 선택 모드도 예전 그대로
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    assert blocked_for("%s/" % target) is False
    assert blocked_for(target) is True

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_stuck_check_threads_have_a_hard_ceiling(monkeypatch, tmp_path):
    """묶음 키가 못 잡은 경우에도 멈춘 스레드 수에 **상한**이 있다.

    묶음 키는 "어디까지가 한 마운트인지" 아는 만큼만 묶어 준다. 서로 다른
    폴더가 사실은 같은 죽은 마운트였다면 키가 갈리므로, 마지막 방어로 스레드
    수 자체를 막는다 — 멈춘 스레드는 D 상태라 죽일 수 없다.
    """
    import threading

    from custom_file_dialog import safety

    monkeypatch.setattr(safety_mounts, "iter_mounts", lambda refresh=False: [])
    monkeypatch.setattr(safety_mounts, "table_available", lambda: False)
    safety.clear_cache()

    release = threading.Event()

    def never_returns(path):
        release.wait(10)
        return True

    try:
        for index in range(safety_reach.MAX_PENDING_CHECKS + 12):
            folder = tmp_path / ("공유%02d" % index)      # 매번 다른 묶음 키
            folder.mkdir()
            safety.safe_call(never_returns, str(folder / "a.csv"), timeout=0.05)
        assert safety_reach.pending_checks() == safety_reach.MAX_PENDING_CHECKS
    finally:
        release.set()
        safety.clear_cache()


def _sidebar_click(qapp, view, row):
    """사이드바 항목을 **실제로** 클릭한다 (press + release).

    ``clicked.emit`` 이나 ``setCurrentIndex`` 로는 안 된다 — Qt 의 QSidebar 는
    선택 모델의 currentChanged 로 이동하므로, 막는 쪽도 그 앞 단계(press)를
    봐야 하고 테스트도 같은 단계를 거쳐야 의미가 있다.
    """
    from qtpy.QtCore import QPoint, Qt
    from qtpy.QtTest import QTest

    index = view.model().index(row, 0)
    assert index.isValid()
    point = view.visualRect(index).center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    _spin(qapp, 80)
    return index


def test_sidebar_click_cannot_open_a_blocked_place(qapp, shallow_tree):
    """사이드바 클릭이 **막힌 자리에 들어가지도 못하게** 한다.

    여기가 비어 있으면 마지막 방어(bounce)가 유일한 방어가 되는데, 그건 Qt 가
    폴더를 **이미 읽은 뒤**라 마운트 폭주를 못 막는다. 실측(strace)으로 확인한
    구멍이다 — ``/user`` 를 클릭하면 통째로 열리고 형제 계정이 전부 stat 됐다.

    그래서 "결국 어느 폴더에 있나"로는 부족하다(bounce 도 그건 맞춘다).
    **들어간 적이 있는가**(``directoryEntered``)를 본다 — 그 신호가 났다면
    Qt 가 이미 그 폴더를 읽은 뒤다.
    """
    from qtpy.QtCore import QUrl
    from qtpy.QtWidgets import QFileDialog, QListView

    from custom_file_dialog import guard_dialog, safety

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(inner)
    dialog.setSidebarUrls([QUrl.fromLocalFile(root), QUrl.fromLocalFile(inner)])
    dialog.show()
    _spin(qapp, 100)
    guard_dialog(dialog)

    sidebar = dialog.findChild(QListView, "sidebar")
    assert sidebar is not None and sidebar.model().rowCount() == 2

    entered = []
    dialog.directoryEntered.connect(entered.append)

    _sidebar_click(qapp, sidebar, 0)          # 막힌 자리
    assert [p for p in entered if os.path.normpath(p) == os.path.normpath(root)] == []
    assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(inner)

    # 멀쩡한 자리는 그대로 열린다(막느라 다 막아 버린 것이 아님을 못박는다)
    deeper = os.path.join(inner, "proj")
    dialog.setSidebarUrls([QUrl.fromLocalFile(root), QUrl.fromLocalFile(deeper)])
    _spin(qapp, 80)
    _sidebar_click(qapp, sidebar, 1)
    assert os.path.normpath(dialog.directory().absolutePath()) == os.path.normpath(deeper)

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_sidebar_keyboard_move_cannot_open_a_blocked_place(qapp, shallow_tree):
    """방향키로 옮겨 갈 칸이 막힌 자리면 그 키 입력을 삼킨다.

    사이드바 이동은 선택이 바뀌는 순간 일어나므로, 화살표도 **옮겨 갈 칸을
    미리 보고** 막아야 한다. 진짜 키 입력으로는 확인할 수 없다 — 오프스크린
    에서는 창이 활성화되지 않아 QListView 가 화살표를 처리하지 않는다(실측:
    currentIndex 가 -1 에서 안 움직인다). 그래서 판정 자체를 직접 건다.
    """
    from qtpy.QtCore import QEvent, QItemSelectionModel, Qt, QUrl
    from qtpy.QtGui import QKeyEvent
    from qtpy.QtWidgets import QFileDialog, QListView

    from custom_file_dialog import guard_dialog, safety
    from custom_file_dialog.guard import _SidebarBlocker

    root, depth = shallow_tree
    inner = os.path.join(root, "myaccount")
    deeper = os.path.join(inner, "proj")
    safety.configure(min_depth=depth + 1)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(inner)
    dialog.setSidebarUrls(
        [QUrl.fromLocalFile(deeper), QUrl.fromLocalFile(root)]     # 아래가 막힌 자리
    )
    dialog.show()
    _spin(qapp, 100)
    hooks = guard_dialog(dialog)
    blocker = [h for h in hooks if isinstance(h, _SidebarBlocker)][0]

    sidebar = dialog.findChild(QListView, "sidebar")

    assert sidebar.model().rowCount() == 2

    def swallows(row, key):
        # 시그널을 막고 현재 칸을 옮긴다. 그냥 setCurrentIndex 하면 Qt 가 그
        # 자리로 이동하면서 사이드바 선택을 지워 버려(실측: currentIndex 가
        # -1) 무엇을 눌렀는지 셈할 기준이 사라진다.
        selection = sidebar.selectionModel()
        was_blocked = selection.blockSignals(True)
        try:
            selection.setCurrentIndex(
                sidebar.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        finally:
            selection.blockSignals(was_blocked)
        assert sidebar.currentIndex().row() == row
        del blocker.blocked[:]
        event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        return blocker.eventFilter(sidebar, event)

    # 멀쩡한 자리(0) 에서 막힌 자리(1) 로 내려가는 것은 삼킨다
    assert swallows(0, Qt.Key.Key_Down) is True
    assert [os.path.normpath(p) for p in blocker.blocked] == [os.path.normpath(root)]
    # End 로 뛰어도 마찬가지(마지막 칸이 막힌 자리다)
    assert swallows(0, Qt.Key.Key_End) is True
    # 반대 방향 · 멀쩡한 자리로 가는 것은 그대로 통과시킨다
    assert swallows(1, Qt.Key.Key_Up) is False
    assert swallows(1, Qt.Key.Key_Home) is False
    # 목록 밖으로 나가는 이동은 볼 것이 없다
    assert swallows(0, Qt.Key.Key_Up) is False

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_one_check_never_spends_more_than_its_budget(monkeypatch, tmp_path):
    """확인 한 번이 ``timeout`` **한 몫**만 쓴다.

    프로브와 stat 이 각자 timeout 을 쓰면 합이 두 배가 되어, 이 모듈이 약속한
    "정해진 시간만 기다린다"가 깨진다. 그 비용은 다이얼로그를 여는 GUI 스레드가
    그대로 문다 — 응답을 삼키는 마운트 하나가 창 뜨는 시간을 먹었다.
    """
    import threading

    from custom_file_dialog import safety

    mount = str(tmp_path / "원격")
    os.makedirs(mount)
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (mount, "nfs4", "서버:/export")],
    )
    release = threading.Event()
    # 프로브도 stat 도 돌아오지 않는 서버 — 가장 비싼 경우다
    monkeypatch.setattr(
        safety_reach, "probe_host", lambda *a, **k: (release.wait(10), True)[1]
    )
    monkeypatch.setattr(os, "stat", lambda *a, **k: (release.wait(10), None)[1])
    safety.clear_cache()

    try:
        # 1) 프로브부터 안 돌아오는 경우
        started = time.monotonic()
        assert safety.is_reachable(os.path.join(mount, "a.csv"), timeout=0.2) is False
        spent = time.monotonic() - started
        # 예산 한 몫 + 스레드 뒷정리 여유. 두 몫(0.4s)을 쓰면 여기서 걸린다.
        assert spent < 0.35, "확인 한 번에 %.3f 초 — 예산(0.2)을 넘었다" % spent

        # 2) 프로브는 시간을 좀 쓰고 통과, stat 이 안 돌아오는 경우.
        #    stat 이 **남은 예산**이 아니라 처음 예산을 다시 받으면 합이 넘친다.
        safety.clear_cache()
        monkeypatch.setattr(
            safety_reach, "probe_host", lambda *a, **k: (time.sleep(0.15), True)[1]
        )
        started = time.monotonic()
        assert safety.is_reachable(os.path.join(mount, "b.csv"), timeout=0.3) is False
        spent = time.monotonic() - started
        assert spent < 0.40, "프로브 뒤 stat 이 예산을 새로 받았다 (%.3f 초)" % spent
    finally:
        release.set()
        safety.clear_cache()


def test_probe_budget_follows_the_timeout(monkeypatch, tmp_path):
    """프로브 몫이 ``path_timeout`` 을 따라 늘어난다.

    상한을 고정하면 timeout 을 아무리 늘려도 연결이 그보다 느린 **살아 있는**
    서버는 영영 "죽음"으로 판정된다(WAN·VPN·부하). 그러면 "넉넉히 잡으세요"
    라는 안내가 이 단계에서만 거짓이 된다.
    """
    from custom_file_dialog import safety

    assert safety_reach.probe_budget(1.0) == pytest.approx(0.25)
    assert safety_reach.probe_budget(3.0) == pytest.approx(0.75)
    # 예산이 최소값보다 작으면 그 작은 값을 쓴다 — 설정을 넘지 않는다
    assert safety_reach.probe_budget(0.1) == pytest.approx(0.1)
    assert safety_reach.probe_budget(0.05) == pytest.approx(0.05)

    mount = str(tmp_path / "원격")
    os.makedirs(mount)
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (mount, "nfs4", "서버:/export")],
    )
    seen = []
    monkeypatch.setattr(
        safety_reach,
        "probe_host",
        lambda host, port, wait=None, **k: seen.append(wait) or True,
    )
    safety.clear_cache()
    try:
        # 연결이 0.4초 걸리는 서버: 기본 예산으로는 못 기다리지만 넉넉히 주면 된다
        safety.is_reachable(mount, timeout=1.0)
        safety.clear_cache()
        safety.is_reachable(mount, timeout=3.0)
        assert seen[0] == pytest.approx(0.25)
        assert seen[1] == pytest.approx(0.75)
    finally:
        safety.clear_cache()


def test_hung_storage_does_not_freeze_the_dialog(qapp, monkeypatch, tmp_path):
    """**저장소가 멈춰도** 여닫기와 확정이 제한 시간 안에 끝난다.

    이 라이브러리가 막겠다는 사고를 정작 제 저장소에서 냈다. 저장소의 기본
    자리는 ``~/.config`` — 상정하는 **네트워크 홈** 위인데, 분류 목록과 최근
    파일 기록을 맨 ``os.listdir`` / ``os.path.isdir`` 로 읽고 있었다. 홈이
    멈추면 다이얼로그를 여는 것만으로 GUI 가 잡혔다(실측: ``path_timeout=1.0``
    을 주고도 생성 9.04초, 파일을 고른 직후의 기록은 18.04초).

    멈춘 저장소에서는 **빈 목록으로 물러선다** — 사이드바에 분류가 안 보일 뿐
    창은 뜨고, 고른 결과는 그대로 나간다.
    """
    from custom_file_dialog import CustomFileDialog, FavoritesStore, RecentStore

    hung = tmp_path / "hung"
    (hung / "fav" / "즐겨찾기").mkdir(parents=True)
    (hung / "recent" / "최근 파일").mkdir(parents=True)
    work = tmp_path / "작업"
    work.mkdir()
    target = work / "고른파일.csv"
    target.write_text("x")

    # 그 자리를 만지면 돌아오지 않는 것처럼 흉내 낸다
    delay = 3.0
    # ``stat`` 까지 물려야 **인덱스 파일 읽기**가 검사된다 — 그것을 빼먹어서
    # 그 보호를 되돌려도 테스트가 통과했다(실측으로 3.00초 매달리는데도).
    for name in ("scandir", "listdir", "stat"):
        real = getattr(os, name)

        def slow(path, *args, _real=real, **kwargs):
            if str(path).startswith(str(hung)):
                time.sleep(delay)
            return _real(path, *args, **kwargs)

        monkeypatch.setattr(os, name, slow)
    real_isdir = os.path.isdir

    def slow_isdir(path, *args, **kwargs):
        if str(path).startswith(str(hung)):
            time.sleep(delay)
        return real_isdir(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "isdir", slow_isdir)

    # 원격으로 보이게 해야 안전장치가 개입한다
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (str(hung), "nfs", "서버:/vol")],
    )
    safety.configure(timeout=1.0)
    safety.clear_cache()

    favorites = FavoritesStore(base_dir=str(hung / "fav"), create=False)
    recent = RecentStore(base_dir=str(hung / "recent"), create=False)

    budget = delay - 0.5        # 한 번이라도 멈춘 호출을 기다렸으면 넘는다
    started = time.perf_counter()
    dialog = CustomFileDialog(
        None, mode="open_file", directory=str(work),
        favorites=favorites, recent=recent, path_timeout=1.0,
    )
    dialog.show()
    qapp.processEvents()
    spent = time.perf_counter() - started
    assert spent < budget, "다이얼로그를 여는 데 %.2f초 걸렸다" % spent

    started = time.perf_counter()
    recent.record(str(target))          # 파일을 고른 **직후** 자동으로 도는 길
    spent = time.perf_counter() - started
    assert spent < budget, "최근 파일 기록에 %.2f초 걸렸다" % spent

    # 목록을 읽어 가는 공개 API 도 마찬가지다 — 앱이 직접 부르는 길이다.
    # (위의 record 는 앞의 도달 확인에서 걸러지므로 이쪽을 따로 겨눈다.)
    started = time.perf_counter()
    items = recent.items()
    spent = time.perf_counter() - started
    assert spent < budget, "최근 목록을 읽는 데 %.2f초 걸렸다" % spent
    assert items == []

    # 파일을 고른 직후 도는 **링크 -> 원본 되돌리기**도 마찬가지다. 사용자가
    # 즐겨찾기 링크를 고르면 늘 이 길로 온다(실측: 안 거쳤을 때 18.00초).
    link = hung / "fav" / "즐겨찾기" / "고른파일.csv"
    os.symlink(str(target), str(link))
    started = time.perf_counter()
    restored = favorites.resolve_all([str(link)])
    spent = time.perf_counter() - started
    assert spent < budget, "링크를 되돌리는 데 %.2f초 걸렸다" % spent
    assert restored == [str(link)], "못 풀면 준 경로를 그대로 돌려줘야 한다"

    # 분류 폴더 판정은 **목록의 항목마다** 불린다(아이콘 제공자) — 항목 하나에
    # 매달리면 목록 전체가 그만큼 늦는다(실측: 안 거쳤을 때 6.00초).
    started = time.perf_counter()
    favorites.is_category_dir(str(hung / "fav" / "즐겨찾기"))
    spent = time.perf_counter() - started
    assert spent < budget, "분류 판정에 %.2f초 걸렸다" % spent

    # 분류 항목 읽기도(실측: 안 거쳤을 때 27.00초)
    started = time.perf_counter()
    assert favorites.entries("즐겨찾기") == []
    spent = time.perf_counter() - started
    assert spent < budget, "분류 항목 읽기에 %.2f초 걸렸다" % spent

    # 이미 등록됐는지 보는 길도 마찬가지다(분류 폴더 목록 + 인덱스 파일).
    started = time.perf_counter()
    assert favorites.contains("즐겨찾기", str(target)) is False
    spent = time.perf_counter() - started
    assert spent < budget, "등록 여부 확인에 %.2f초 걸렸다" % spent

    # 물러섰을 뿐 창은 살아 있다
    assert favorites.categories() == []
    dialog.done(0)
