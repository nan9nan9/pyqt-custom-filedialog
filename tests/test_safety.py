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

    # 하위 경로와 일반 파일 이름은 그대로 통과
    for text in ("proj", os.path.join(guarded_root, "alice"), "보고서.csv"):
        edit.setText(text)
        assert press_enter() is False, text
        assert click_open() is False, text

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
    monkeypatch.setattr(
        safety_reach, "call_with_timeout", lambda func, *a, **k: (stats.append(a), (True, None))[1]
    )

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

        # 만지는 판정과 safe_* 도 같은 규칙 — 디스크 접근 없이 즉시 False
        assert not safety.is_reachable(str(root / "j"))
        assert safety.safe_isdir(str(root / "myaccount")) is False
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
        assert time.time() - start < 0.1        # 기다림 없이 즉시
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
        assert time.time() - start < 0.1
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
        subprocess.run(
            ["strace", "-f", "-e", "trace=%file", "-o", str(trace),
             sys.executable, str(script)],
            env=env, check=True, capture_output=True, timeout=120,
        )
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
    """차단 경로 자체는 확정 불가, 하위는 허용(문서된 규칙 그대로)."""
    from custom_file_dialog import safety

    assert not safety.may_open(guarded_root)
    assert safety.may_open(os.path.join(guarded_root, "myaccount"))


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
