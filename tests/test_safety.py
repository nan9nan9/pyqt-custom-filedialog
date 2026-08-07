"""죽은 네트워크 경로 방어 · 차단 경로(guarded_roots) · 자동완성 제한."""

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


def test_safety_mount_lookup(dead_nfs):
    """마운트 표만 보고 원격 여부와 서버를 알아낸다(파일시스템 미접근)."""
    safety = dead_nfs["safety"]
    mount = dead_nfs["mount"]

    assert safety.is_remote(os.path.join(mount, "a", "b.csv"))
    assert not safety.is_remote("/etc/hosts")
    assert safety.mount_for(os.path.join(mount, "x"))[1] == "nfs4"
    assert safety.server_of("nfs1.corp:/export/proj") == "nfs1.corp"
    assert safety.server_of("//winsrv/share") == "winsrv"
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


def test_safety_extra_probes(dead_nfs, monkeypatch):
    """LDAP 처럼 경로만 봐서는 모르는 의존 서비스도 등록해 검사한다."""
    safety = dead_nfs["safety"]
    state = dead_nfs["state"]
    state["probe_ok"] = True
    state["stat_hangs"] = False

    safety.configure(probes=[("ldap.corp", 389)])
    try:
        assert safety.settings()["probes"] == [("ldap.corp", 389)]
        safety.is_reachable(os.path.join(dead_nfs["mount"], "x"), timeout=0.2)
        assert ("ldap.corp", 389) in state["probes"]
    finally:
        safety.configure(probes=[])


def test_guarded_root_blocks_itself_only(guarded_root):
    """그 자리 자체만 막고, 하위 경로는 평소대로 쓴다."""
    from custom_file_dialog import safety

    assert safety.guarded_roots() == [os.path.normpath(guarded_root)]

    assert safety.is_guarded(guarded_root)
    assert safety.is_guarded(guarded_root + os.sep)          # 끝의 / 는 무시
    assert not safety.is_guarded(os.path.join(guarded_root, "jekai"))
    assert not safety.is_guarded(guarded_root + "s")         # 이름만 비슷한 건 아님

    # 접근 판정과 os.path 대체 함수에도 그대로 반영된다
    assert not safety.is_reachable(guarded_root)
    assert safety.is_reachable(os.path.join(guarded_root, "jekai"))
    assert safety.safe_isdir(guarded_root) is False          # 실제로는 폴더지만 안 만진다
    assert safety.safe_isdir(os.path.join(guarded_root, "jekai")) is True


def test_guarded_root_in_validation(qapp, guarded_root):
    """차단 경로를 입력하면 '없는 경로'로 보고, 하위 경로는 정상 판정한다."""
    edit = FilePathEdit(mode="directory")

    edit.set_path(guarded_root)
    assert not edit.is_valid()

    edit.set_path(os.path.join(guarded_root, "jekai"))
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

    inner = os.path.join(guarded_root, "jekai")
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

    inner = os.path.join(guarded_root, "jekai")
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
    assert safety.path_depth("/user/jekai") == 2
    assert safety.path_depth("/user/jekai/proj") == 3
    assert safety.path_depth("") == 0
    # 상대 경로는 절대 경로로 편 뒤에 센다
    assert safety.path_depth("jekai") == safety.path_depth(os.getcwd()) + 1


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
    assert not safety.is_too_shallow(os.path.join(root, "jekai"))

    # 나열만 막는 설정이라 경로 자체의 접근 판정은 건드리지 않는다
    assert safety.is_reachable(root)
    assert safety.safe_isdir(root) is True


def test_min_depth_blocks_completer_listing(qapp, shallow_tree):
    """`/user/j` 처럼 쳐도 그 폴더를 읽지 않는다(한 단계 아래는 정상)."""
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

    inner = os.path.join(root, "jekai")
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
    assert candidates(os.path.join(root, "j")) == ["jane", "jekai", "joe"]

    safety.configure(min_depth=depth + 1)
    assert candidates(os.path.join(root, "j")) == []
    # 한 단계 아래에서는 그대로 완성된다
    assert candidates(os.path.join(root, "jekai", "p")) == ["proj"]


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

    try:
        safety.configure(allow_listing=False)
        dialog = QFileDialog()
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        assert hooks_module.guard_dialog(dialog) == ["completer"]

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

    # 자동완성 모델만 바꾼다. 얕은 자리를 "못 들어가게" 하는 설정은 아니므로
    # 이벤트 필터(더블클릭 · 확정 차단)까지 걸지는 않는다.
    assert installed == ["completer"]
    name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
    assert isinstance(name_edit.completer().model(), GuardedFileSystemModel)

    dialog.deleteLater()


def test_min_depth_off_installs_nothing(qapp):
    """둘 다 꺼져 있으면 다이얼로그에 아무것도 걸지 않는다."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import safety

    safety.reset()
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


def test_guard_dialog_noop_without_guarded_roots(qapp, tmp_path):
    """차단 경로가 없으면 아무것도 걸지 않는다."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import guard_dialog, safety

    safety.configure(guarded_roots=[])
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

    inner = os.path.join(guarded_root, "jekai")
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

    # 현재 폴더가 /user/jekai 이므로 경로 체인에 /user 가 들어 있다
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

    inner = os.path.join(guarded_root, "jekai")
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

    inner = os.path.join(guarded_root, "jekai")
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
        safety,
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

