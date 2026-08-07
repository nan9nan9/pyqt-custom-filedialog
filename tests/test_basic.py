"""오프스크린 환경에서 동작하는 기본 스모크 테스트.

QApplication 이 필요하므로 QT_QPA_PLATFORM=offscreen 로 실행한다.
실제 QFileDialog 는 띄우지 않고 dialog.exec_file_dialog 를 monkeypatch 한다.
"""

import atexit
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from qtpy.QtCore import QDir, QMimeData, QPoint, QSettings, QUrl  # noqa: E402

# Qt 는 파일 다이얼로그의 사이드바/히스토리/뷰 모드를 사용자 설정(리눅스 기준
# ~/.config/QtProject.conf)에 영구 저장한다. 테스트가 실제 설정을 건드리지
# 않도록 QSettings 저장 위치를 임시 폴더로 돌린다.
# (QApplication 생성 전에 해야 하고, QFileDialog 내부는 NativeFormat 으로
#  QSettings 를 만들기 때문에 두 포맷 모두 경로를 바꿔 줘야 한다)
_SETTINGS_DIR = tempfile.mkdtemp(prefix="custom-file-dialog-test-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _SETTINGS_DIR)
    QSettings.setPath(_fmt, QSettings.Scope.SystemScope, _SETTINGS_DIR)
atexit.register(shutil.rmtree, _SETTINGS_DIR, True)
from qtpy.QtGui import QDropEvent  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

from custom_file_dialog import (  # noqa: E402
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
from custom_file_dialog.history import PathHistory  # noqa: E402
from custom_file_dialog import dialog as dialog_module  # noqa: E402
from custom_file_dialog import history as history_module  # noqa: E402
from custom_file_dialog import hooks as hooks_module  # noqa: E402
from custom_file_dialog import places as places_module  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fake_dialog(monkeypatch):
    """다이얼로그를 띄우는 대신 미리 정한 결과를 돌려주도록 바꾼다."""
    calls = []
    result = {"paths": [], "filter": ""}

    def fake(**kwargs):
        calls.append(kwargs)
        return list(result["paths"]), result["filter"]

    monkeypatch.setattr(dialog_module, "exec_file_dialog", fake)
    return {"calls": calls, "result": result}


# --------------------------------------------------------------- 필터 헬퍼
def test_build_filter():
    assert build_filter([("이미지", ["png", "jpg"])], add_all_files=False) == (
        "이미지 (*.png *.jpg)"
    )
    # 확장자는 "png" / ".png" / "*.png" 어느 형태로 줘도 된다
    assert build_filter([("문서", [".txt", "*.md", "rst"])], add_all_files=False) == (
        "문서 (*.txt *.md *.rst)"
    )
    # 이미 Qt 필터 문자열이면 그대로 통과
    assert build_filter("모두 (*)") == "모두 (*)"
    # add_all_files 는 "모든 파일 (*)" 을 뒤에 붙인다 (이미 있으면 중복 안 됨)
    assert build_filter([("CSV", ["csv"])], add_all_files=True) == (
        "CSV (*.csv);;모든 파일 (*)"
    )
    assert build_filter([("CSV", ["csv"]), ("전체", ["*"])], add_all_files=True) == (
        "CSV (*.csv);;전체 (*)"
    )
    assert build_filter(None) is None


def test_suffix_and_ensure():
    assert suffix_of("이미지 (*.png *.jpg)") == "png"
    assert suffix_of("모든 파일 (*)") is None       # 확장자를 특정할 수 없음
    assert ensure_suffix("/tmp/out", "csv") == "/tmp/out.csv"
    assert ensure_suffix("/tmp/out.json", "csv") == "/tmp/out.json"  # 이미 있으면 유지
    assert ensure_suffix("", "csv") == ""


# ----------------------------------------------------------------- 유효성
def test_validate_paths(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    ok, _ = validate_paths([str(target)], mode=SelectMode.OPEN_FILE)
    assert ok
    # 없는 파일 -> 무효
    ok, reason = validate_paths([str(tmp_path / "없음.txt")], mode=SelectMode.OPEN_FILE)
    assert not ok and "존재하지 않습니다" in reason
    # 파일 자리에 폴더 -> 무효
    ok, reason = validate_paths([str(tmp_path)], mode=SelectMode.OPEN_FILE)
    assert not ok and "폴더입니다" in reason
    # 저장 모드는 아직 없는 파일이 정상 (상위 폴더만 있으면 됨)
    ok, _ = validate_paths([str(tmp_path / "새파일.csv")], mode=SelectMode.SAVE_FILE)
    assert ok
    ok, reason = validate_paths(
        [str(tmp_path / "없는폴더" / "x.csv")], mode=SelectMode.SAVE_FILE
    )
    assert not ok and "상위 폴더" in reason
    # 비어 있으면 required 여부에 따라 갈린다
    assert validate_paths([], required=False)[0]
    assert not validate_paths([], required=True)[0]


# ------------------------------------------- 죽은 네트워크 경로 방어 (safety)
@pytest.fixture
def dead_nfs(monkeypatch, tmp_path):
    """응답 없는 NFS 마운트를 흉내 낸다.

    실제로 멈추는 마운트를 만들 수 없으므로, 마운트 표와 소켓 프로브,
    그리고 os.stat 을 갈아 끼워 "영원히 안 돌아오는 경로"를 만든다.
    """
    from custom_file_dialog import safety

    mountpoint = str(tmp_path / "nfs")
    os.mkdir(mountpoint)

    safety.clear_cache()
    monkeypatch.setattr(
        safety,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (mountpoint, "nfs4", "nfs1.corp:/export/proj"),
        ],
    )

    # probe_ok: 소켓 프로브 결과 / stat_hangs: stat 이 안 돌아오는지 (따로 조절)
    state = {"probe_ok": False, "stat_hangs": True, "probes": [], "stat_calls": 0}

    def fake_probe(host, port, timeout=None):
        state["probes"].append((host, port))
        return state["probe_ok"]

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path).startswith(mountpoint):
            state["stat_calls"] += 1
            if state["stat_hangs"]:
                time.sleep(1.0)         # 제한 시간(0.2초)보다 훨씬 길게
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(safety, "probe_host", fake_probe)
    monkeypatch.setattr(os, "stat", fake_stat)

    yield {"mount": mountpoint, "state": state, "safety": safety}
    safety.clear_cache()


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


@pytest.fixture
def guarded_root(tmp_path):
    """마운트가 잔뜩 달린 ``/user`` 같은 자리를 흉내 낸다."""
    from custom_file_dialog import safety

    root = tmp_path / "user"
    root.mkdir()
    for name in ("jekai", "alice", "bob"):
        (root / name).mkdir()
    (root / "jekai" / "proj").mkdir()

    safety.configure(guarded_roots=[str(root)])
    yield str(root)
    safety.configure(guarded_roots=[])
    safety.clear_cache()


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


# ------------------------------------------ 자동완성 최소 깊이 (min_depth)
@pytest.fixture
def shallow_tree(tmp_path):
    """얕은 자리를 흉내 낸다 — 그 폴더의 깊이를 함께 돌려준다.

    실제 ``/user`` (깊이 1)는 테스트에서 만들 수 없으므로, tmp 폴더의 깊이를
    재서 "이 폴더가 딱 한 단계 모자라는" min_depth 를 걸어 같은 상황을 만든다.
    """
    from custom_file_dialog import safety

    root = tmp_path / "user"
    root.mkdir()
    for name in ("jekai", "jane", "joe"):
        (root / name).mkdir()
    (root / "jekai" / "proj").mkdir()

    yield str(root), safety.path_depth(str(root))
    safety.reset()


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
    from custom_file_dialog.hooks import _ItemBlocker

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


def _guarded_dialog_in(qapp, directory):
    """차단 경로 안(``/user/jekai``)에서 연 다이얼로그와 설치된 장치들."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import guard_dialog

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setDirectory(directory)
    dialog.show()
    _spin(qapp, 500)
    return dialog, guard_dialog(dialog)


def test_combo_blocker_swallows_guarded_entry(qapp, guarded_root):
    """"Look in" 드롭다운에서 차단 경로를 고를 수 없다(다른 항목은 정상)."""
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QKeyEvent, QMouseEvent
    from qtpy.QtWidgets import QComboBox

    from custom_file_dialog.hooks import _ItemBlocker

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

    from custom_file_dialog.hooks import _AcceptBlocker

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

    from custom_file_dialog.hooks import _AcceptBlocker, _ItemBlocker

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


# ---------------------------------------------------- QFileDialog 래퍼 자체
def test_exec_file_dialog_dispatch(qapp, monkeypatch):
    """모드별로 알맞은 QFileDialog 정적 메서드가 호출되고 결과가 정규화된다."""
    from qtpy.QtWidgets import QFileDialog

    seen = {}

    def record(name, ret):
        def fake(*args):
            seen[name] = args
            return ret

        return staticmethod(fake)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", record("open", ("/a.txt", "텍스트 (*.txt)"))
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", record("opens", (["/a.txt", "/b.txt"], ""))
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", record("save", ("/out", "CSV (*.csv)"))
    )
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", record("dir", "/some/dir"))

    paths, chosen = dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE)
    assert paths == ["/a.txt"] and chosen == "텍스트 (*.txt)"

    paths, _ = dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILES)
    assert paths == ["/a.txt", "/b.txt"]

    # 저장 모드는 선택된 필터에서 확장자를 유추해 붙여 준다
    paths, _ = dialog_module.exec_file_dialog(mode=SelectMode.SAVE_FILE)
    assert paths == ["/out.csv"]

    # 디렉터리 모드는 문자열 하나만 돌려주므로 리스트로 감싼다
    paths, _ = dialog_module.exec_file_dialog(mode=SelectMode.DIRECTORY)
    assert paths == ["/some/dir"]

    # native=False 면 DontUseNativeDialog 옵션이 켜져서 전달된다
    dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE, native=False)
    options = seen["open"][-1]
    assert options & dialog_module._option("DontUseNativeDialog")

    dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE, native=True)
    assert not (seen["open"][-1] & dialog_module._option("DontUseNativeDialog"))


def test_options_are_accepted_by_qt(qapp):
    """조립한 options 값을 실제 QFileDialog 가 받아들이는지 확인한다."""
    from qtpy.QtWidgets import QFileDialog

    dlg = QFileDialog()
    dlg.setOptions(dialog_module.make_options(native=False, show_dirs_only=True))
    assert dlg.options() & dialog_module._option("ShowDirsOnly")
    assert dlg.options() & dialog_module._option("DontUseNativeDialog")


def test_cancel_returns_empty(qapp, monkeypatch):
    from qtpy.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a: ("", ""))
    )
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a: "")
    )
    assert dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE)[0] == []
    assert dialog_module.exec_file_dialog(mode=SelectMode.DIRECTORY)[0] == []


# --------------------------------------------------------------- 사이드바
def test_to_urls():
    from qtpy.QtCore import QUrl

    urls = to_urls(["~", "/mnt/data", QUrl("ftp://host/pub"), "file:///tmp", "file:", ""])
    assert urls[0].toLocalFile() == os.path.expanduser("~")
    assert urls[1].toLocalFile() == "/mnt/data"
    assert urls[2].toString() == "ftp://host/pub"   # QUrl 은 그대로 통과
    assert urls[3].toString() == "file:///tmp"      # 스킴 있는 문자열은 URL 로
    assert urls[4].toString() == "file:"            # 사이드바의 "Computer" 항목
    assert len(urls) == 5                           # 빈 문자열은 버려진다

    # 윈도우 드라이브 문자는 스킴이 아니라 경로로 본다
    assert to_urls([r"C:\data"])[0].toLocalFile().endswith("data")


def test_current_sidebar_urls_reads_settings(qapp):
    """Qt 가 사이드바를 저장하는 설정 키를 직접 읽는다(부작용 없음).

    QFileDialog 인스턴스를 만들어 sidebarUrls() 를 읽는 방식은 위젯 생성 전에
    빈 목록을 주기도 하고, 창을 닫을 때 사용자 설정을 덮어쓴다. 회귀 방지용.
    """
    from qtpy.QtCore import QUrl

    settings = QSettings(QSettings.Scope.UserScope, "QtProject")
    settings.remove("FileDialog/shortcuts")
    settings.sync()

    # 저장된 것이 없으면 Qt 기본값(Computer + 홈)
    defaults = places_module.current_sidebar_urls()
    assert [u.toString() for u in defaults][0] == "file:"        # Computer
    assert len(defaults) == 2

    # 저장된 값이 있으면 그 값을 그대로 읽는다
    settings.setValue(
        "FileDialog/shortcuts", [QUrl("file:"), QUrl.fromLocalFile("/mnt/data")]
    )
    settings.sync()
    saved = places_module.current_sidebar_urls()
    assert [u.toLocalFile() for u in saved] == ["", "/mnt/data"]

    # 뒤에 덧붙이기
    combined = saved + to_urls(["/srv/입력"])
    assert combined[-1].toLocalFile() == "/srv/입력"
    assert len(combined) == len(saved) + 1

    settings.remove("FileDialog/shortcuts")
    settings.sync()


def test_current_sidebar_urls_has_no_side_effects(qapp, tmp_path):
    """조회만으로 설정 파일이 만들어지거나 바뀌면 안 된다."""
    settings = QSettings(QSettings.Scope.UserScope, "QtProject")
    settings.sync()
    path = settings.fileName()
    before = os.path.getmtime(path) if os.path.exists(path) else None

    places_module.current_sidebar_urls()

    after = os.path.getmtime(path) if os.path.exists(path) else None
    assert before == after


def test_sidebar_urls_applied_to_dialog(qapp, monkeypatch, tmp_path):
    """sidebar_urls 를 주면 인스턴스 다이얼로그에 setSidebarUrls 가 적용된다."""
    from qtpy.QtWidgets import QFileDialog

    seen = {}
    original_set = QFileDialog.setSidebarUrls

    def spy(self, urls):
        seen["urls"] = list(urls)
        seen["options"] = self.options()
        seen["file_mode"] = self.fileMode()
        original_set(self, urls)

    monkeypatch.setattr(QFileDialog, "setSidebarUrls", spy)
    # 다이얼로그를 실제로 띄우지 않고 '취소' 로 닫은 것처럼 처리한다
    monkeypatch.setattr(QFileDialog, "exec_", lambda self: 0, raising=False)
    monkeypatch.setattr(QFileDialog, "exec", lambda self: 0, raising=False)

    paths, _ = dialog_module.exec_file_dialog(
        mode=SelectMode.OPEN_FILES,
        directory=str(tmp_path),
        places=Places(sidebar_urls=["/mnt/data", str(tmp_path)]),
    )
    assert paths == []                                  # 취소
    assert [u.toLocalFile() for u in seen["urls"]] == ["/mnt/data", str(tmp_path)]
    # 사이드바를 쓰려면 네이티브 창을 못 쓰므로 자동으로 꺼져 있어야 한다
    assert seen["options"] & dialog_module._option("DontUseNativeDialog")
    assert seen["file_mode"] == dialog_module._enum("FileMode", "ExistingFiles")


def test_sidebar_urls_from_widget(qapp, monkeypatch, tmp_path):
    """FilePathEdit 에 준 sidebar_urls 가 다이얼로그까지 전달된다."""
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return [], ""

    monkeypatch.setattr(dialog_module, "exec_file_dialog", fake)

    def local(places):
        return [u.toLocalFile() for u in places.sidebar_urls()]

    edit = FilePathEdit(mode="open_file", sidebar_urls=["~/작업"])
    edit.browse()
    assert local(seen["places"]) == [os.path.expanduser("~/작업")]

    # 실행 중에 바꾸거나(리스트) 끌 수 있다(None)
    edit.set_sidebar_urls(["/mnt/data"])
    assert edit.sidebar_urls() == ["/mnt/data"]
    edit.browse()
    assert local(seen["places"]) == ["/mnt/data"]

    edit.set_sidebar_urls(None)
    assert edit.sidebar_urls() is None
    edit.browse()
    assert not seen["places"]                # 얹을 게 없다 -> 정적 메서드 경로

    # 기본값은 커스터마이즈하지 않음
    assert FilePathEdit(mode="open_file").sidebar_urls() is None


def test_sidebar_dialog_returns_selection(qapp, monkeypatch, tmp_path):
    """인스턴스 경로에서도 선택 결과/확장자 보정이 정적 경로와 같게 동작한다."""
    from qtpy.QtWidgets import QFileDialog

    target = tmp_path / "결과"
    monkeypatch.setattr(QFileDialog, "exec_", lambda self: 1, raising=False)
    monkeypatch.setattr(QFileDialog, "exec", lambda self: 1, raising=False)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [str(target)])
    monkeypatch.setattr(QFileDialog, "selectedNameFilter", lambda self: "CSV (*.csv)")

    paths, chosen = dialog_module.exec_file_dialog(
        mode=SelectMode.SAVE_FILE,
        directory=str(target),
        name_filter="CSV (*.csv);;모든 파일 (*)",
        places=Places(sidebar_urls=[str(tmp_path)]),
    )
    assert paths == [str(target) + ".csv"]
    assert chosen == "CSV (*.csv)"


# --------------------------------------------------------------- 즐겨찾기
@pytest.fixture
def store(tmp_path):
    return FavoritesStore(base_dir=str(tmp_path / "favorites"))


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


def test_favorites_base_dir_configuration(qapp, tmp_path, monkeypatch):
    """즐겨찾기 저장 위치를 앱 전체 기본값 / 인스턴스별로 정할 수 있다."""
    from custom_file_dialog import (
        configure_favorites,
        configured_base_dir,
        default_base_dir,
    )
    from custom_file_dialog import favorites as favorites_module

    # 테스트가 전역 상태를 남기지 않도록 원래 값으로 되돌린다
    monkeypatch.setattr(favorites_module, "_CONFIGURED_BASE_DIR", None)

    os_default = default_base_dir()
    assert configured_base_dir() is None
    assert os_default.endswith(favorites_module.DEFAULT_DIRNAME)

    # 1) 앱 전체 기본 위치 지정
    wanted = str(tmp_path / "회사" / "즐겨찾기")
    assert configure_favorites(wanted) == wanted
    assert configured_base_dir() == wanted
    assert default_base_dir() == wanted
    assert FavoritesStore().base_dir == wanted          # base_dir 없이 만들면 여기로

    # 2) 인스턴스별 지정이 전체 기본값보다 우선한다
    other = str(tmp_path / "다른곳")
    assert FavoritesStore(base_dir=other).base_dir == other

    # 3) ~ 표기가 펼쳐진다
    assert FavoritesStore(base_dir="~/.fdw-테스트", create=False).base_dir == (
        os.path.join(os.path.expanduser("~"), ".fdw-테스트")
    )

    # 4) None 으로 지정 취소하면 OS 표준 위치로 복귀
    assert configure_favorites(None) == os_default
    assert configured_base_dir() is None
    assert FavoritesStore(create=False).base_dir == os_default


def test_favorites_base_dir_is_created(qapp, tmp_path):
    """지정한 위치가 없으면 만들어 주고, 그 아래에 분류가 생긴다."""
    base = tmp_path / "깊은" / "경로" / "즐겨찾기"
    assert not base.exists()

    store = FavoritesStore(base_dir=str(base))
    assert base.is_dir()

    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    assert (base / "설계").is_dir()
    assert store.items("설계") == [design]


def test_favorites_add_and_list(store, tmp_path):
    design, report, output = _make_tree(tmp_path)

    store.add("설계", design)
    store.add("설계", output)          # 폴더도 등록된다
    store.add("보고서", report)

    assert store.categories() == ["보고서", "설계"]
    assert sorted(store.items("설계")) == sorted([design, output])
    assert store.items("보고서") == [report]

    # 사이드바에는 분류 '폴더' 들이 들어간다
    urls = [u.toLocalFile() for u in store.sidebar_urls()]
    assert urls == [store.category_dir("보고서"), store.category_dir("설계")]

    # 분류 폴더 안에는 원본 이름의 링크가 만들어진다
    names = [name for name, _target in store.entries("설계")]
    assert names == ["산출물", "설계도.csv"]


def test_favorites_resolve(store, tmp_path):
    """즐겨찾기에서 고른 링크 경로가 원본으로 복원된다."""
    design, _report, output = _make_tree(tmp_path)
    link = store.add("설계", design)
    dir_link = store.add("설계", output)

    assert link != design                    # 링크 경로는 분류 폴더 안
    assert store.resolve(link) == design
    assert store.resolve(dir_link) == output
    assert store.resolve_all([link, dir_link]) == [design, output]

    # 즐겨찾기 폴더 밖의 경로는 손대지 않는다(실제 심볼릭 링크여도)
    outside = tmp_path / "바깥링크.csv"
    os.symlink(design, str(outside))
    assert store.resolve(str(outside)) == str(outside)
    assert store.resolve("") == ""


def test_favorites_duplicates_and_names(store, tmp_path):
    design, _report, _output = _make_tree(tmp_path)

    first = store.add("설계", design)
    again = store.add("설계", design)
    assert first == again                    # 같은 대상은 두 번 안 만든다
    assert len(store.items("설계")) == 1

    # 이름만 같고 대상이 다르면 번호가 붙는다
    other_dir = tmp_path / "projC"
    other_dir.mkdir()
    other = other_dir / "설계도.csv"
    other.write_text("y")
    second = store.add("설계", str(other))
    assert os.path.basename(second) == "설계도 (2).csv"
    assert sorted(store.items("설계")) == sorted([design, str(other)])

    # 이미 등록된 대상은 이름을 다시 줘도 새로 만들지 않는다(중복 방지 우선)
    store.add("설계", str(other), name="사본.csv")
    assert [name for name, _t in store.entries("설계")] == [
        "설계도 (2).csv",
        "설계도.csv",
    ]

    # 표시 이름은 새 대상을 등록할 때 지정한다
    third = other_dir / "부록.csv"
    third.write_text("z")
    store.add("설계", str(third), name="사본.csv")
    assert ("사본.csv", str(third)) in store.entries("설계")


def test_favorites_remove(store, tmp_path):
    design, report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    store.add("설계", report)

    assert store.contains("설계", design)
    assert store.remove("설계", design)      # 원본 경로로 제거
    assert not store.contains("설계", design)
    assert os.path.exists(design)            # 원본은 그대로
    assert store.items("설계") == [report]

    assert store.remove("설계", "보고서.md")  # 표시 이름으로도 제거
    assert store.items("설계") == []
    assert not store.remove("설계", "없음")

    store.add("보고서", report)
    store.remove_category("보고서")
    assert "보고서" not in store.categories()
    assert os.path.exists(report)


def test_favorites_errors(store, tmp_path):
    with pytest.raises(FavoritesError):
        store.add("설계", str(tmp_path / "없는파일.csv"))
    with pytest.raises(ValueError):
        store.add_category("  ")
    with pytest.raises(ValueError):
        FavoritesStore(base_dir=str(tmp_path), link_mode="복사")

    # 분류 이름의 경로 구분자는 안전하게 치환된다
    store.add_category("a/b")
    assert "a_b" in store.categories()


def test_favorites_in_dialog(qapp, tmp_path, monkeypatch):
    """분류 폴더가 사이드바에 실제로 뜨고, 그 안에 항목들이 보인다."""
    from qtpy.QtWidgets import QFileDialog, QListView, QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, report, output = _make_tree(tmp_path)
    store.add("설계", design)
    store.add("설계", output)
    store.add("보고서", report)

    captured = {}

    def fake_exec(self):
        view = self.findChild(QListView, "sidebar")
        model = view.model()
        captured["사이드바"] = [
            model.index(i, 0).data() for i in range(model.rowCount())
        ]
        # 분류 폴더로 이동하면 오른쪽에 등록 항목들이 나온다
        self.setDirectory(store.category_dir("설계"))
        _spin(qapp)
        tree = self.findChild(QTreeView, "treeView")
        tree_model, root = tree.model(), tree.rootIndex()
        captured["오른쪽"] = [
            tree_model.index(i, 0, root).data()
            for i in range(tree_model.rowCount(root))
        ]
        return 0

    monkeypatch.setattr(QFileDialog, "exec_", fake_exec, raising=False)
    monkeypatch.setattr(QFileDialog, "exec", fake_exec, raising=False)

    edit = FilePathEdit(mode="open_file", favorites=store)
    edit.browse()

    # Computer 바로 뒤에 분류가 끼워진다 (Computer, 보고서, 설계, 홈 …)
    _assert_at_end(captured["사이드바"], ["보고서", "설계"])
    # 파일과 폴더가 함께 나온다
    assert sorted(captured["오른쪽"]) == sorted(["산출물", "설계도.csv"])


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


# ------------------------------------------------------------- 최근 파일
@pytest.fixture
def recent(tmp_path):
    return RecentStore(base_dir=str(tmp_path / "recent"), max_items=3)


def _touch(tmp_path, name):
    path = tmp_path / name
    path.write_text("x")
    return str(path)


def test_recent_records_newest_first(recent, tmp_path):
    """기록한 순서의 역순(최신순)으로 나온다."""
    first = _touch(tmp_path, "가.csv")
    second = _touch(tmp_path, "나.csv")

    recent.record(first)
    time.sleep(0.02)        # 링크 mtime 으로 순서를 판단하므로 시각을 벌린다
    recent.record(second)

    assert recent.items() == [second, first]
    assert [name for name, _t in recent.entries()] == ["나.csv", "가.csv"]


def test_recent_rerecord_moves_to_front(recent, tmp_path):
    """이미 있는 파일을 다시 고르면 맨 앞으로 올라온다(중복 생기지 않음)."""
    first = _touch(tmp_path, "가.csv")
    second = _touch(tmp_path, "나.csv")

    recent.record(first)
    time.sleep(0.02)
    recent.record(second)
    time.sleep(0.02)
    recent.record(first)

    assert recent.items() == [first, second]
    assert len(recent.links()) == 2      # 링크가 늘어나지 않는다


def test_recent_max_items(recent, tmp_path):
    """개수를 넘기면 오래된 것부터 지운다."""
    paths = []
    for name in ("1.csv", "2.csv", "3.csv", "4.csv"):
        paths.append(_touch(tmp_path, name))
        recent.record(paths[-1])
        time.sleep(0.02)

    assert recent.max_items == 3
    assert recent.items() == paths[:0:-1]                # 최신 3개, 최신순
    assert len(os.listdir(recent.category_dir(recent.name))) == 3
    assert all(os.path.exists(p) for p in paths)         # 원본은 그대로

    recent.set_max_items(1)
    assert recent.items() == [paths[-1]]


def test_recent_ignores_folders_and_own_links(recent, tmp_path):
    """폴더와 자기 자신의 링크는 기록하지 않는다."""
    folder = tmp_path / "폴더"
    folder.mkdir()
    assert recent.record(str(folder)) is None
    assert recent.record(str(tmp_path / "없는파일.csv")) is None
    assert recent.record("") is None

    target = _touch(tmp_path, "가.csv")
    link = recent.record(target)
    assert link is not None
    # 링크 경로를 그대로 다시 넣어도 중복 기록되지 않는다
    assert recent.record(link) is None
    assert recent.items() == [target]


def test_recent_resolve_and_sidebar(recent, tmp_path):
    """링크는 원본으로 복원되고, 사이드바에는 항목 하나로 나온다."""
    target = _touch(tmp_path, "가.csv")
    link = recent.record(target)

    assert link != target
    assert recent.resolve(link) == target
    urls = [u.toLocalFile() for u in recent.sidebar_urls()]
    assert urls == [recent.category_dir(recent.name)]
    assert recent.is_category_dir(recent.category_dir(recent.name))


def test_recent_clear_keeps_sidebar_entry(recent, tmp_path):
    """비우면 목록만 사라지고 사이드바 항목은 남는다."""
    target = _touch(tmp_path, "가.csv")
    recent.record(target)

    recent.clear()
    assert recent.items() == []
    assert os.path.isdir(recent.category_dir(recent.name))   # 항목 유지
    assert recent.sidebar_urls()
    assert os.path.exists(target)                            # 원본 보존


def test_recent_max_zero_disables(tmp_path):
    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=0)
    assert store.record(_touch(tmp_path, "가.csv")) is None
    assert store.items() == []


def test_widget_recent_option(qapp, tmp_path, monkeypatch):
    """recent_files 옵션: 기본 꺼짐, True 면 자동 생성, 저장소 직접 지정도 가능."""
    # 기본은 꺼져 있어 아무것도 만들지 않는다
    plain = FilePathEdit(mode="open_file")
    assert plain.recent_files() is None
    assert plain.recent_items() == []

    # 저장소를 직접 넘기면 그대로 쓴다
    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    edit = FilePathEdit(mode="open_file", recent_files=store)
    assert edit.recent_files() is store

    # 실행 중 끄고 켜기
    edit.set_recent_files(False)
    assert edit.recent_files() is None
    edit.set_recent_files(store)
    assert edit.recent_files() is store


def test_widget_records_on_browse(qapp, tmp_path, monkeypatch):
    """다이얼로그에서 고르면 최근 목록에 자동으로 쌓인다."""
    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    first = _touch(tmp_path, "가.csv")
    second = _touch(tmp_path, "나.csv")

    picked = {"paths": [first]}
    monkeypatch.setattr(
        dialog_module, "exec_file_dialog", lambda **kw: (list(picked["paths"]), "")
    )

    edit = FilePathEdit(mode="open_file", recent_files=store)
    edit.browse()
    time.sleep(0.02)
    picked["paths"] = [second]
    edit.browse()

    assert edit.recent_items() == [second, first]

    # 즐겨찾기/최근 항목에서 고르면 원본 경로로 복원해서 기록한다
    link = store.link_for(store.name, first)
    picked["paths"] = [link]
    edit.browse()
    assert edit.path() == first
    assert edit.recent_items()[0] == first


def test_widget_recent_sidebar_and_icon(qapp, tmp_path, monkeypatch):
    """사이드바에 최근 파일이 덧붙고, 시계 아이콘이 붙는다."""
    from qtpy.QtCore import QFileInfo
    from custom_file_dialog import FavoritesStore, RecentStore, clock_icon

    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    store.record(design)

    seen = {}
    monkeypatch.setattr(
        dialog_module,
        "exec_file_dialog",
        lambda **kw: (seen.update(kw), ([], ""))[1],
    )

    edit = FilePathEdit(mode="open_file", favorites=favorites, recent_files=store)
    edit.browse()

    paths = [
        u.toLocalFile() if hasattr(u, "toLocalFile") else u
        for u in seen["places"].sidebar_urls()
    ]
    # 홈 -> 현재 위치 -> 최근 파일 -> 북마크 순서
    at = _assert_at_end(
        paths, [store.category_dir(store.name), favorites.category_dir("설계")]
    )
    assert at > 0                               # 홈 등 고정 자리가 앞에 남는다
    assert paths[0] == QDir.homePath()
    assert seen["places"].recent is store

    # 최근 파일은 시계, 즐겨찾기는 별표
    provider = seen["places"].icon_provider()
    recent_icon = provider.icon(QFileInfo(store.category_dir(store.name)))
    favorite_icon = provider.icon(QFileInfo(favorites.category_dir("설계")))

    def key(icon):
        return icon.pixmap(16, 16).toImage().pixel(8, 8)

    assert key(recent_icon) == key(clock_icon())
    assert favorite_icon is provider.star()
    assert key(recent_icon) != key(provider.star())


def test_sidebar_default_order(qapp, tmp_path, monkeypatch):
    """기본 순서: 홈 -> 현재 위치 -> 최근 파일 -> 북마크(분류 이름순).

    Qt 기본 "Computer" 항목은 넣지 않는다.
    """
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    favorites.add("보고서", design)
    store.record(design)

    seen = {}
    monkeypatch.setattr(
        dialog_module,
        "exec_file_dialog",
        lambda **kw: (seen.update(kw), ([], ""))[1],
    )

    # start_dir 을 줘서 "현재 위치"를 확정적으로 만든다
    here = str(tmp_path / "projA")
    edit = FilePathEdit(mode="open_file", favorites=favorites,
                        recent_files=store, start_dir=here)
    edit.browse()

    paths = [u.toLocalFile() for u in seen["places"].sidebar_urls(here)]
    assert paths == [
        QDir.homePath(),                     # 홈
        here,                                # 현재 위치
        store.category_dir(store.name),      # 최근 파일
        favorites.category_dir("보고서"),     # 북마크 분류(이름순)
        favorites.category_dir("설계"),
    ]
    assert "" not in paths                   # Computer 항목 없음

    # 위젯이 스스로 계산해도 같은 결과
    assert [u.toLocalFile() for u in edit.effective_sidebar_urls()] == paths


def test_sidebar_current_dir_deduped(qapp, tmp_path):
    """홈에서 열면 "현재 위치"가 홈과 겹치므로 하나만 남는다."""
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    places = Places(favorites=favorites)

    home = QDir.homePath()
    assert [u.toLocalFile() for u in places.sidebar_urls(home)] == [
        home,
        favorites.category_dir("설계"),
    ]
    # 다른 곳에서 열면 두 자리가 다 나온다
    assert [u.toLocalFile() for u in places.sidebar_urls(str(tmp_path))] == [
        home,
        str(tmp_path),
        favorites.category_dir("설계"),
    ]


def test_sidebar_marks_home_and_current(qapp, tmp_path):
    """홈은 집 아이콘으로, 현재 위치는 "현재 위치"라는 이름으로 표시한다."""
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    places = Places(favorites=favorites)

    here = str(tmp_path)
    marks = places.sidebar_marks(here)
    assert set(marks) == {QDir.homePath(), here}

    home_label, home_mark = marks[QDir.homePath()]
    assert home_label is None                    # 이름은 Qt 가 붙인 그대로 둔다
    assert not home_mark.isNull()
    assert home_mark.pixmap(16, 16).toImage() == home_icon().pixmap(16, 16).toImage()

    assert marks[here] == (places_module.CURRENT_LABEL, None)  # 아이콘은 폴더 그대로
    assert places_module.CURRENT_LABEL == "현재 위치"


def test_sidebar_marks_skip_home_when_current(qapp, tmp_path):
    """홈에서 열면 두 항목이 하나로 합쳐지므로 홈 표시만 남는다."""
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)

    marks = Places(favorites=favorites).sidebar_marks(QDir.homePath())
    assert set(marks) == {QDir.homePath()}
    assert marks[QDir.homePath()][0] is None     # "현재 위치"로 부르지 않는다


def test_sidebar_marks_respect_options(qapp, tmp_path):
    """사이드바를 직접 주거나 아이콘을 끄면 그만큼만 손댄다."""
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    here = str(tmp_path)

    # 기준 목록을 직접 준 경우엔 "현재 위치" 항목을 붙이지 않았으므로 이름도 없다
    given = Places(favorites=favorites, sidebar_urls=["~", here])
    assert set(given.sidebar_marks(here)) == {QDir.homePath()}

    # icon=False 면 홈도 Qt 기본 폴더 아이콘 그대로
    plain = Places(favorites=favorites, icon=False)
    assert set(plain.sidebar_marks(here)) == {here}

    # 사이드바에 얹을 게 없어 손대지 않는 경우엔 표시도 바꾸지 않는다
    assert Places().sidebar_marks(here) == {}


def test_sidebar_marks_applied_to_dialog(qapp, tmp_path):
    """다이얼로그 사이드바가 실제로 바뀐 이름·아이콘으로 그려진다."""
    from qtpy.QtWidgets import QFileDialog, QListView, QStyleOptionViewItem

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    places = Places(favorites=favorites)
    here = str(tmp_path)

    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setDirectory(here)
    dialog.setSidebarUrls(to_urls(places.sidebar_urls(here)))
    delegate = hooks_module.mark_sidebar(dialog, places, here)
    assert delegate is not None

    # 델리게이트는 다이얼로그에 매달려 있어야 산다. 사이드바는 findChild 로 그때
    # 그때 만들어지는 임시 래퍼라 거기 매달면 파이썬 객체가 수거된다.
    assert delegate.parent() is dialog
    sidebar = dialog.findChild(QListView, "sidebar")
    assert sidebar.itemDelegate() is delegate

    def drawn(row):
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, sidebar.model().index(row, 0))
        return option

    home, current = drawn(0), drawn(1)
    assert home.text == os.path.basename(QDir.homePath())   # 이름은 그대로
    assert home.icon.pixmap(16, 16).toImage() == home_icon().pixmap(16, 16).toImage()
    assert current.text == "현재 위치"
    assert current.icon.pixmap(16, 16).toImage() != home.icon.pixmap(16, 16).toImage()

    dialog.deleteLater()


def test_sidebar_marks_keep_disabled_look(qapp, tmp_path):
    """열 수 없는 위치를 흐리게 하던 Qt 기본 델리게이트의 처리를 이어받는다."""
    from qtpy.QtWidgets import QFileDialog, QListView, QStyle, QStyleOptionViewItem

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    places = Places(favorites=favorites)
    here = str(tmp_path)

    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setDirectory(here)
    # 없는 경로를 하나 섞으면 Qt 가 그 항목의 EnabledRole 을 False 로 둔다
    missing = str(tmp_path / "없어진폴더")
    dialog.setSidebarUrls(to_urls(places.sidebar_urls(here)) + to_urls([missing]))
    delegate = hooks_module.mark_sidebar(dialog, places, here)

    sidebar = dialog.findChild(QListView, "sidebar")
    enabled_flag = getattr(QStyle, "StateFlag", QStyle).State_Enabled

    def enabled(row):
        option = QStyleOptionViewItem()
        option.state |= enabled_flag         # 뷰가 그릴 때처럼 켜 두고 시작한다
        delegate.initStyleOption(option, sidebar.model().index(row, 0))
        return bool(option.state & enabled_flag)

    rows = sidebar.model().rowCount()
    assert all(enabled(row) for row in range(rows - 1))   # 홈 · 현재 위치 · 분류
    assert not enabled(rows - 1)                          # 없어진 폴더

    dialog.deleteLater()


def test_home_icon(qapp):
    """집 아이콘이 요청한 크기로 그려진다."""
    icon = home_icon(sizes=(16, 32))
    assert not icon.isNull()
    assert sorted((s.width(), s.height()) for s in icon.availableSizes()) == [
        (16, 16),
        (32, 32),
    ]
    image = icon.pixmap(32, 32).toImage()
    assert image.pixelColor(16, 24).alpha() == 0      # 아래 가운데는 문으로 뚫려 있다
    assert image.pixelColor(16, 8).alpha() > 0        # 지붕은 칠해져 있다
    assert image.pixelColor(0, 0).alpha() == 0        # 모서리는 비어 있다


def test_clock_icon(qapp):
    """시계 아이콘이 요청한 크기로 그려진다."""
    from custom_file_dialog import clock_icon

    icon = clock_icon(sizes=(16, 32))
    assert not icon.isNull()
    assert sorted((s.width(), s.height()) for s in icon.availableSizes()) == [
        (16, 16),
        (32, 32),
    ]
    image = icon.pixmap(32, 32).toImage()
    # 테두리 원이 그려지므로 가장자리 근처에 칠해진 픽셀이 있고, 모서리는 비어 있다
    assert any(
        image.pixelColor(x, 16).alpha() > 0 for x in range(image.width())
    )
    assert image.pixelColor(0, 0).alpha() == 0


# ----------------------------------------------- 링크 폴더 -> 실제 경로 이동
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


# ----------------------------------------- 우클릭 메뉴 (FavoritesMenus)
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


def test_add_to_favorites_menu(qapp, tmp_path):
    """파일 목록 우클릭에 "즐겨찾기에 추가" 가 붙고, Qt 기본 항목도 남는다."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog, menus = _menu_dialog(store, os.path.dirname(design))
    dialog.show()
    _spin(qapp, 400)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    rows = {model.index(r, 0, root).data(): model.index(r, 0, root)
            for r in range(model.rowCount(root))}

    path, menu = _view_menu(menus, tree, rows["설계도.csv"])
    assert path == design

    submenu = _submenu_of(menu)
    assert submenu is not None
    labels = [a.text() for a in submenu.actions() if not a.isSeparator()]
    assert labels == ["설계", "새 분류..."]
    # 이미 등록된 분류는 비활성
    assert not submenu.actions()[0].isEnabled()

    # Qt 기본 항목이 그대로 따라붙는다
    texts = [a.text() for a in menu.actions() if not a.isSeparator() and a.menu() is None]
    assert any("Rename" in t for t in texts)
    assert any("Delete" in t for t in texts)
    assert any("hidden" in t for t in texts)
    dialog.close()


def test_add_to_favorites_action(qapp, tmp_path):
    """메뉴 동작이 실제로 등록하고 사이드바까지 갱신한다."""
    from qtpy.QtWidgets import QListView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog, menus = _menu_dialog(store, str(tmp_path))
    added = []
    menus.favoriteAdded.connect(lambda c, p: added.append((c, p)))

    # 기존 분류에 추가
    assert menus.add_to_favorites(output, "설계")
    assert added == [("설계", output)]
    assert sorted(store.items("설계")) == sorted([design, output])

    # 새 분류에 추가 -> 사이드바에도 바로 나타난다
    assert menus.add_to_favorites(design, "자료")
    assert store.categories() == ["설계", "자료"]

    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()
    names = [model.index(r, 0).data() for r in range(model.rowCount())]
    assert "자료" in names

    # 없는 경로는 실패로 알린다(예외를 밖으로 던지지 않는다)
    assert not menus.add_to_favorites("", "설계")


def test_add_to_favorites_skips_links(qapp, tmp_path):
    """분류 폴더 안의 링크에는 "즐겨찾기에 추가" 가 뜨지 않는다."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    recent.record(design)

    dialog, menus = _menu_dialog([store, recent], store.category_dir("설계"))
    dialog.show()
    _spin(qapp, 400)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    assert model.rowCount(root) == 1

    _path, menu = _view_menu(menus, tree, model.index(0, 0, root))
    assert _submenu_of(menu) is None        # 추가 메뉴 없음
    # 그래도 Qt 기본 항목은 남는다
    assert menu.actions()
    dialog.close()


def test_remove_entry_menu_replaces_delete(qapp, tmp_path):
    """분류 안에서는 "삭제" 대신 "'분류'에서 제거" 가 나온다."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    recent.record(design)

    def labels_in(directory):
        dialog, menus = _menu_dialog([store, recent], directory)
        dialog.show()
        _spin(qapp, 400)
        tree = dialog.findChild(QTreeView, "treeView")
        model, root = tree.model(), tree.rootIndex()
        rows = {model.index(r, 0, root).data(): model.index(r, 0, root)
                for r in range(model.rowCount(root))}
        _path, menu = _view_menu(menus, tree, rows["설계도.csv"])
        texts = _menu_labels(menu)
        dialog.close()
        return texts

    # 즐겨찾기 분류 -> 분류 이름이 그대로 메뉴에 들어간다
    favorite_labels = labels_in(store.category_dir("설계"))
    assert "'설계'에서 제거" in favorite_labels
    assert not any("Delete" in t for t in favorite_labels)
    assert any("Rename" in t for t in favorite_labels)      # 이름 바꾸기는 남는다

    # 최근 파일 -> 항목 이름이 그대로 메뉴에 들어간다
    recent_labels = labels_in(recent.category_dir(recent.name))
    assert "'최근 파일'에서 제거" in recent_labels
    assert not any("Delete" in t for t in recent_labels)

    # 보통 폴더는 예전 그대로 (Qt 기본 "삭제" 가 있고 "제거" 는 없다)
    plain_labels = labels_in(os.path.dirname(design))
    assert any("Delete" in t for t in plain_labels)
    assert not any("에서 제거" in t for t in plain_labels)


def test_remove_entry_keeps_original_file(qapp, tmp_path):
    """제거는 링크만 지운다 — 원본 파일과 다른 항목은 그대로."""
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    store.add("설계", report)

    _dialog, menus = _menu_dialog(store, store.category_dir("설계"))
    removed = []
    menus.entryRemoved.connect(lambda c, p: removed.append((c, p)))

    link = os.path.join(store.category_dir("설계"), "설계도.csv")
    assert menus.remove_entry(store, "설계", link)

    assert store.items("설계") == [report]          # 그 항목만 빠졌다
    assert os.path.exists(design)                   # 원본은 그대로
    assert removed == [("설계", design)]            # 시그널은 원본 경로로
    assert not os.path.lexists(link)

    # 없는 항목을 다시 빼려 하면 조용히 False
    assert not menus.remove_entry(store, "설계", link)
    assert not menus.remove_entry(None, "설계", link)
    assert not menus.remove_entry(store, "설계", "")


def test_remove_entry_only_direct_children(qapp, tmp_path):
    """분류 폴더 **바로 아래** 항목에만 붙는다(링크 안쪽은 원본 쪽 규칙)."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    _design, _report, output = _make_tree(tmp_path)
    (tmp_path / "projB" / "산출물" / "안쪽").mkdir()
    store.add("설계", output)

    dialog, menus = _menu_dialog(store, store.category_dir("설계"))
    dialog.show()
    _spin(qapp, 400)
    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()

    # 분류 바로 아래의 폴더 링크 -> 제거 메뉴
    index = model.index(0, 0, root)
    store_at, category, _link = menus.entry_at(tree, index)
    assert store_at is store and category == "설계"

    # 링크를 따라 들어간 안쪽은 원본이므로 손대지 않는다
    dialog.setDirectory(output)
    _spin(qapp, 400)
    inner_root = tree.rootIndex()
    inner = model.index(0, 0, inner_root)
    assert menus.entry_at(tree, inner) == (None, None, None)
    dialog.close()


def test_remove_entry_menu_without_favorites(qapp, tmp_path):
    """최근 파일만 써도 파일 목록에 "제거" 메뉴가 걸린다."""
    from qtpy.QtWidgets import QTreeView

    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    recent.record(design)

    dialog, menus = _menu_dialog(recent, recent.category_dir(recent.name))
    assert menus._places.favorites_store() is None
    dialog.show()
    _spin(qapp, 400)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    _path, menu = _view_menu(menus, tree, model.index(0, 0, root))
    assert "'최근 파일'에서 제거" in _menu_labels(menu)
    assert _submenu_of(menu) is None            # 즐겨찾기가 없으니 추가 메뉴도 없다
    dialog.close()


def test_add_menu_can_be_disabled(qapp, tmp_path, monkeypatch):
    """add_menu=False 면 파일 목록 메뉴는 건드리지 않고 사이드바만 건다."""
    from qtpy.QtWidgets import QFileDialog, QListView, QTreeView

    from custom_file_dialog import FavoritesMenus

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setSidebarUrls(store.sidebar_urls())
    dialog.setDirectory(str(tmp_path))

    taken = []
    monkeypatch.setattr(
        FavoritesMenus, "_take_over", staticmethod(lambda w, h: taken.append(w))
    )

    assert FavoritesMenus(dialog, _places_of(store), confirm=False, add_menu=False).install()
    assert taken == [dialog.findChild(QListView, "sidebar")]     # 사이드바만

    taken.clear()
    assert FavoritesMenus(dialog, _places_of(store), confirm=False, add_menu=True).install()
    assert dialog.findChild(QTreeView, "treeView") in taken      # 파일 목록도


def test_sidebar_menu_targets_categories_only(qapp, tmp_path):
    """분류 항목과 일반 항목을 구분한다."""
    from qtpy.QtWidgets import QListView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    plain = str(tmp_path / "projA")
    dialog, menu = _menu_dialog(store, str(tmp_path), extra_sidebar=[plain])
    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()

    found = {}
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        found[index.data()] = menu.category_at(index)

    assert found["설계"] == "설계"          # 분류 -> 이름
    assert found["projA"] is None           # 일반 폴더 -> 분류가 아님


def test_sidebar_menu_removes_plain_entry(qapp, tmp_path):
    """사이드바에 끌어다 놓은 일반 폴더는 우클릭으로 뺄 수 있다(Qt 기본 Remove)."""
    from qtpy.QtWidgets import QListView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    plain = str(tmp_path / "projA")
    dialog, menu = _menu_dialog(store, str(tmp_path), extra_sidebar=[plain])
    removed = []
    menu.sidebarEntryRemoved.connect(removed.append)

    assert menu.remove_sidebar_entry(plain)
    assert removed == [plain]

    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()
    names = [model.index(r, 0).data() for r in range(model.rowCount())]
    assert "projA" not in names
    assert "설계" in names                  # 분류는 그대로

    assert os.path.isdir(plain)             # 폴더 자체는 남는다
    assert not menu.remove_sidebar_entry(plain)     # 이미 없으면 False
    assert not menu.remove_sidebar_entry("")


def test_sidebar_menu_fixed_urls(qapp, tmp_path):
    """제거를 막을 위치를 지정할 수 있고, 기본은 사용자 홈이다."""
    from qtpy.QtCore import QUrl
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import FavoritesMenus

    assert Places().fixed_urls() == [QDir.homePath()]

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    keep = str(tmp_path / "지킬폴더")
    free = str(tmp_path / "뺄폴더")
    os.mkdir(keep)
    os.mkdir(free)

    def make(fixed):
        dialog = QFileDialog()
        dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
        dialog.setSidebarUrls(
            [
                QUrl.fromLocalFile(QDir.homePath()),
                QUrl.fromLocalFile(keep),
                QUrl.fromLocalFile(free),
            ]
            + store.sidebar_urls()
        )
        dialog.setDirectory(str(tmp_path))
        menus = FavoritesMenus(
            dialog, Places(favorites=store, fixed_urls=fixed), confirm=False
        )
        assert menus.install()
        return menus

    # 기본(None) -> 홈만 보호
    menus = make(None)
    assert menus.is_fixed(QDir.homePath())
    assert not menus.is_fixed(keep)
    assert not menus.remove_sidebar_entry(QDir.homePath())    # 막힌다
    assert menus.remove_sidebar_entry(free)                   # 일반 항목은 제거됨

    # 직접 지정 -> 나열한 위치도 보호
    menus = make([QDir.homePath(), keep])
    assert menus.is_fixed(keep)
    assert not menus.remove_sidebar_entry(keep)
    assert sorted(menus.fixed_sidebar_urls()) == sorted(
        [os.path.normpath(QDir.homePath()), os.path.normpath(keep)]
    )

    # 빈 목록 -> 아무것도 보호하지 않음(홈도 뺄 수 있다)
    menus = make([])
    assert not menus.is_fixed(QDir.homePath())
    assert menus.remove_sidebar_entry(QDir.homePath())


def test_widget_passes_fixed_urls(qapp, tmp_path, monkeypatch):
    """FilePathEdit 이 보호 위치 설정을 다이얼로그까지 전달한다."""
    seen = {}
    monkeypatch.setattr(
        dialog_module,
        "exec_file_dialog",
        lambda **kw: (seen.update(kw), ([], ""))[1],
    )

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    edit = FilePathEdit(mode="open_file", favorites=store)
    edit.browse()
    assert seen["places"].fixed_urls() == [QDir.homePath()]   # 기본 = 홈만 보호
    assert edit.fixed_sidebar_urls() is None

    edit.set_fixed_sidebar_urls(["/srv/공용"])
    edit.browse()
    assert seen["places"].fixed_urls() == ["/srv/공용"]
    assert edit.fixed_sidebar_urls() == ["/srv/공용"]

    edit.set_fixed_sidebar_urls([])
    edit.browse()
    assert seen["places"].fixed_urls() == []


def test_sidebar_menu_keeps_computer_entry(qapp, tmp_path):
    """"Computer" 처럼 경로가 없는 항목은 Qt 와 마찬가지로 뺄 수 없다."""
    from qtpy.QtCore import QUrl
    from qtpy.QtWidgets import QFileDialog, QListView

    from custom_file_dialog import FavoritesMenus
    from custom_file_dialog.menus import URL_ROLE

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    dialog.setSidebarUrls([QUrl("file:")] + store.sidebar_urls())
    dialog.setDirectory(str(tmp_path))
    menu = FavoritesMenus(dialog, _places_of(store), confirm=False)
    assert menu.install()

    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()
    computer = [
        model.index(r, 0)
        for r in range(model.rowCount())
        if model.index(r, 0).data() == "Computer"
    ][0]

    url = computer.data(URL_ROLE)
    assert not url.toLocalFile()            # 경로가 비어 있다 -> 메뉴에서 비활성
    assert not menu.remove_sidebar_entry(url.toLocalFile())


def test_sidebar_menu_removes_category(qapp, tmp_path):
    """분류 삭제는 메뉴 동작으로 이뤄지고, 사이드바까지 정리된다."""
    from qtpy.QtWidgets import QListView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    store.add("보고서", report)

    dialog, menu = _menu_dialog(store, str(tmp_path))
    removed = []
    menu.categoryRemoved.connect(removed.append)

    assert menu.remove_category("보고서")
    assert removed == ["보고서"]
    assert store.categories() == ["설계"]

    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()
    names = [model.index(r, 0).data() for r in range(model.rowCount())]
    assert "보고서" not in names and "설계" in names

    assert os.path.exists(report)                   # 원본은 그대로


def test_sidebar_menu_clears_recent(qapp, tmp_path):
    """최근 파일 항목의 메뉴는 '삭제'가 아니라 '목록 비우기'다."""
    from qtpy.QtWidgets import QListView
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)
    store.record(design)

    dialog, menu = _menu_dialog([favorites, store], str(tmp_path))
    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()

    kinds = {}
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        found, name = menu.store_at(index)
        if found is not None:
            kinds[name] = menu._places.is_recent(found)

    assert kinds == {store.name: True, "설계": False}

    cleared = []
    menu.recentCleared.connect(cleared.append)
    assert menu.clear_recent(store, store.name)
    assert cleared == [store.name]

    assert store.items() == []
    assert os.path.isdir(store.category_dir(store.name))    # 항목 자체는 남는다
    assert os.path.exists(design)                           # 원본 보존
    assert favorites.items("설계") == [design]               # 즐겨찾기는 그대로


def test_sidebar_menu_replaces_qt_default(qapp, tmp_path):
    """Qt 기본 사이드바 메뉴("Remove")를 우리 것으로 갈아 끼운다.

    분류가 아닌 자리를 우클릭하면 아무 메뉴도 뜨지 않아야 한다.
    (분류 위 우클릭은 모달 메뉴를 띄우므로 여기서 직접 눌러 보지 않는다)
    """
    from qtpy.QtCore import QPoint, Qt
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QListView, QMenu

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog, menu = _menu_dialog(store, str(tmp_path))
    sidebar = dialog.findChild(QListView, "sidebar")
    dialog.show()
    _spin(qapp, 300)

    assert sidebar.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu

    # 항목이 없는 아래쪽 빈 영역 -> 분류가 아니므로 메뉴가 뜨지 않는다
    empty = QPoint(sidebar.viewport().width() // 2, sidebar.viewport().height() - 2)
    assert not sidebar.indexAt(empty).isValid()

    QTest.mouseClick(sidebar.viewport(), Qt.MouseButton.RightButton, pos=empty)
    _spin(qapp, 150)
    visible = [w for w in qapp.topLevelWidgets() if isinstance(w, QMenu) and w.isVisible()]
    assert visible == []            # Qt 기본 "Remove" 메뉴도 뜨지 않는다
    dialog.close()


def test_sidebar_menu_installed_through_widget(qapp, tmp_path, monkeypatch):
    """FilePathEdit 이 저장소를 다이얼로그까지 전달한다."""
    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)

    seen = {}
    monkeypatch.setattr(
        dialog_module,
        "exec_file_dialog",
        lambda **kw: (seen.update(kw), ([], ""))[1],
    )

    edit = FilePathEdit(mode="open_file", favorites=favorites, recent_files=store)
    edit.browse()
    assert seen["places"].favorites is favorites
    assert seen["places"].recent is store

    # 즐겨찾기/최근을 안 쓰면 넘길 저장소도 없다
    plain = FilePathEdit(mode="open_file")
    plain.browse()
    assert not seen["places"]



# ------------------------------------------------------------ FilePathEdit
def test_set_and_get_path(qapp, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    edit = FilePathEdit(mode="open_file")
    changed = []
    edit.pathChanged.connect(changed.append)

    edit.set_path(str(target))
    assert edit.path() == str(target)
    assert edit.paths() == [str(target)]
    assert changed == [str(target)]
    assert edit.is_valid()

    edit.set_path(str(tmp_path / "없음.txt"))
    assert not edit.is_valid()
    assert "존재하지 않습니다" in edit.invalid_reason()

    edit.clear()
    assert edit.path() == ""
    assert edit.is_valid()      # required=False 이므로 빈 값은 정상


def test_single_mode_keeps_one_path(qapp, tmp_path):
    """단일 선택 모드에 여러 경로를 넣어도 첫 번째만 남는다."""
    edit = FilePathEdit(mode="open_file")
    edit.set_paths(["/a/b.txt", "/c/d.txt"])
    assert edit.paths() == ["/a/b.txt"]

    multi = FilePathEdit(mode="open_files")
    multi.set_paths(["/a/b.txt", "/c/d.txt"])
    assert multi.paths() == ["/a/b.txt", "/c/d.txt"]
    assert "; " in multi.text()


def test_validity_signal(qapp, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    edit = FilePathEdit(mode="open_file")
    states = []
    edit.validityChanged.connect(states.append)

    edit.set_path(str(tmp_path / "없음.txt"))
    assert states == [False]
    edit.set_path(str(target))
    assert states == [False, True]
    # 같은 유효 상태가 이어지면 시그널이 다시 나오지 않는다
    edit.set_path(str(target))
    assert states == [False, True]


def test_browse_fills_path(qapp, fake_dialog, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    fake_dialog["result"]["paths"] = [str(target)]

    edit = FilePathEdit(mode="open_file", filters=[("텍스트", ["txt"])])
    browsed = []
    edit.browsed.connect(browsed.append)

    result = edit.browse()
    assert result == [str(target)]
    assert edit.path() == str(target)
    assert browsed == [[str(target)]]

    # 다이얼로그에는 조립된 Qt 필터 문자열이 그대로 전달된다
    kwargs = fake_dialog["calls"][0]
    assert kwargs["name_filter"] == "텍스트 (*.txt);;모든 파일 (*)"
    assert kwargs["mode"] == SelectMode.OPEN_FILE


def test_browse_cancel_keeps_value(qapp, fake_dialog, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    edit = FilePathEdit(mode="open_file")
    edit.set_path(str(target))
    fake_dialog["result"]["paths"] = []      # 취소

    assert edit.browse() == []
    assert edit.path() == str(target)        # 기존 값 유지


def test_save_mode_appends_suffix(qapp, fake_dialog, tmp_path):
    """저장 모드에서 확장자를 안 쓰면 default_suffix 가 붙는다."""
    fake_dialog["result"]["paths"] = [str(tmp_path / "결과")]

    edit = FilePathEdit(mode="save_file", default_suffix="csv")
    edit.browse()
    assert edit.path() == str(tmp_path / "결과.csv")

    # 필터에서 확장자를 유추하는 경우
    fake_dialog["result"]["paths"] = [str(tmp_path / "보고서")]
    fake_dialog["result"]["filter"] = "JSON (*.json)"
    edit2 = FilePathEdit(mode="save_file", filters=[("JSON", ["json"])])
    edit2.browse()
    assert edit2.path() == str(tmp_path / "보고서.json")


def test_start_dir_priority(qapp, fake_dialog, tmp_path):
    """다이얼로그 초기 위치: 현재 값 > start_dir > 최근 폴더 > cwd."""
    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "a.txt"
    target.write_text("x")

    edit = FilePathEdit(mode="open_file", start_dir=str(tmp_path))
    fake_dialog["result"]["paths"] = []

    # 값이 없으면 start_dir 에서 시작
    edit.browse()
    assert fake_dialog["calls"][-1]["directory"] == str(tmp_path)

    # 값이 있으면 그 파일이 있는 폴더에서 시작
    edit.set_path(str(target))
    edit.browse()
    assert fake_dialog["calls"][-1]["directory"] == str(sub)


def test_history_remembers_selection(qapp, fake_dialog, tmp_path):
    first = tmp_path / "1.txt"
    second = tmp_path / "2.txt"
    for path in (first, second):
        path.write_text("x")

    edit = FilePathEdit(mode="open_file", history=5)
    fake_dialog["result"]["paths"] = [str(first)]
    edit.browse()
    fake_dialog["result"]["paths"] = [str(second)]
    edit.browse()

    # 최신순으로 쌓인다
    assert edit.history_items() == [str(second), str(first)]

    # 메뉴에서 고르면 그 경로가 입력창에 채워진다
    edit._rebuild_history_menu()
    actions = [a for a in edit._history_menu.actions() if a.text() == str(first)]
    assert actions
    actions[0].trigger()
    assert edit.path() == str(first)


def test_drop_file(qapp, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    edit = FilePathEdit(mode="open_file")
    _drop(edit, [str(target)])
    assert edit.path() == str(target)

    # 파일 모드에 폴더를 떨어뜨리면 무시된다
    edit.clear()
    _drop(edit, [str(tmp_path)])
    assert edit.path() == ""

    # 폴더 모드에 파일을 떨어뜨리면 그 파일이 든 폴더로 받는다
    dir_edit = FilePathEdit(mode="directory")
    _drop(dir_edit, [str(target)])
    assert dir_edit.path() == str(tmp_path)


def test_drop_multiple_files(qapp, tmp_path):
    files = []
    for name in ("a.txt", "b.txt"):
        path = tmp_path / name
        path.write_text("x")
        files.append(str(path))

    multi = FilePathEdit(mode="open_files")
    _drop(multi, files)
    assert multi.paths() == files

    single = FilePathEdit(mode="open_file")
    _drop(single, files)
    assert single.paths() == files[:1]      # 단일 모드는 첫 개만


def _drop(widget, paths):
    """실제 드롭 이벤트를 만들어 위젯에 전달한다."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    from qtpy.QtCore import Qt

    event = QDropEvent(
        QPoint(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(event)


def test_set_mode_switches_defaults(qapp, tmp_path):
    """모드를 바꾸면 must_exist 기본값도 함께 바뀐다."""
    edit = FilePathEdit(mode="open_file")
    missing = str(tmp_path / "없음.csv")
    edit.set_path(missing)
    assert not edit.is_valid()          # open 모드: 없으면 무효

    edit.set_mode("save_file")
    assert edit.is_valid()              # save 모드: 없어도 정상


def test_read_only_and_native_toggle(qapp):
    edit = FilePathEdit(mode="open_file", read_only=True)
    assert edit.line_edit.isReadOnly()
    edit.set_read_only(False)
    assert not edit.line_edit.isReadOnly()

    edit.set_native(False)
    assert edit._native is False


# ------------------------------------------------------------ FilePathForm
def test_form_values_and_validity(qapp, tmp_path):
    target = tmp_path / "in.csv"
    target.write_text("x")

    form = FilePathForm()
    form.add_path("input", "입력 파일:", mode="open_file", required=True)
    form.add_path("outdir", "출력 폴더:", mode="directory", required=True)
    form.add_path("extra", "추가 파일:", mode="open_files")

    assert not form.is_valid()          # required 인데 비어 있음
    assert {k for k, _ in form.invalid_items()} == {"input", "outdir"}

    form.set_values({"input": str(target), "outdir": str(tmp_path)})
    assert form.is_valid()
    assert form.values() == {
        "input": str(target),
        "outdir": str(tmp_path),
        "extra": [],                    # open_files 줄은 리스트로 나온다
    }

    form.clear()
    assert not form.is_valid()
    assert form.keys() == ["input", "outdir", "extra"]

    with pytest.raises(ValueError):
        form.add_path("input", "중복:", mode="open_file")


def test_form_signals(qapp, tmp_path):
    target = tmp_path / "in.csv"
    target.write_text("x")

    form = FilePathForm()
    form.add_path("input", "입력:", mode="open_file", required=True)
    changes = []
    valid_states = []
    form.valueChanged.connect(lambda k, p: changes.append((k, p)))
    form.validityChanged.connect(valid_states.append)

    form.edit("input").set_path(str(target))
    assert changes == [("input", str(target))]
    assert valid_states == [True]


# --------------------------------------------------------------- 히스토리
def test_path_history_persists(tmp_path):
    """settings_key 를 주면 QSettings 에 저장되어 다시 읽힌다."""
    ini = str(tmp_path / "test.ini")
    settings = QSettings(ini, QSettings.Format.IniFormat)

    history = PathHistory(key="demo", max_items=3, settings=settings)
    for path in ("/a", "/b", "/c", "/d"):
        history.add(path)
    history.set_last_dir("/x")
    assert history.items() == ["/d", "/c", "/b"]        # 최신 3개만
    settings.sync()

    # 같은 저장소를 다시 읽어도 유지된다
    reloaded = PathHistory(
        key="demo", max_items=3, settings=QSettings(ini, QSettings.Format.IniFormat)
    )
    assert reloaded.items() == ["/d", "/c", "/b"]
    assert reloaded.last_dir() == "/x"

    # 중복 추가는 맨 위로 끌어올린다
    history.add("/b")
    assert history.items() == ["/b", "/d", "/c"]

    history.clear()
    assert history.items() == []


# ------------------------------------------- 용도별 시작 위치 (remember / settings_key)
def _dialog_start_dirs(monkeypatch):
    """exec_file_dialog 이 실제로 어느 폴더에서 열었는지 기록하는 스파이."""
    seen = []
    result = {"paths": []}

    def fake_run(parent, mode, caption, directory, *args, **kwargs):
        seen.append(directory)
        return list(result["paths"]), ""

    monkeypatch.setattr(dialog_module, "_run_dialog", fake_run)
    return seen, result


def test_remember_keeps_start_dir_per_purpose(qapp, tmp_path, monkeypatch):
    """용도(remember)마다 마지막에 쓰던 폴더를 따로 기억한다."""
    from custom_file_dialog import exec_file_dialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    csv_dir = tmp_path / "입력csv"
    out_dir = tmp_path / "결과"
    csv_dir.mkdir()
    out_dir.mkdir()
    (csv_dir / "a.csv").write_text("x")
    (out_dir / "r.json").write_text("x")

    seen, result = _dialog_start_dirs(monkeypatch)

    # 기억이 없으면 현재 작업 디렉터리에서 연다
    result["paths"] = [str(csv_dir / "a.csv")]
    exec_file_dialog(mode="open_file", remember="입력csv")
    assert seen[-1] == os.getcwd()

    result["paths"] = [str(out_dir / "r.json")]
    exec_file_dialog(mode="save_file", remember="결과저장")
    assert seen[-1] == os.getcwd()

    # 다시 열면 각자 자기가 마지막에 쓰던 폴더에서 연다
    result["paths"] = []
    exec_file_dialog(mode="open_file", remember="입력csv")
    assert seen[-1] == str(csv_dir)
    exec_file_dialog(mode="save_file", remember="결과저장")
    assert seen[-1] == str(out_dir)

    # 서로 섞이지 않는다
    assert last_dir("입력csv") == str(csv_dir)
    assert last_dir("결과저장") == str(out_dir)
    assert last_dir("한번도안쓴용도") is None


def test_remember_off_by_default(qapp, tmp_path, monkeypatch):
    """remember 를 주지 않으면 아무것도 기억하지 않는다(기존 동작)."""
    from custom_file_dialog import exec_file_dialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)
    target = tmp_path / "a.csv"
    target.write_text("x")

    seen, result = _dialog_start_dirs(monkeypatch)
    result["paths"] = [str(target)]

    exec_file_dialog(mode="open_file")
    assert seen[-1] == ""                       # 시작 폴더를 정하지 않는다
    assert last_dir("") is None


def test_remember_respects_explicit_directory(qapp, tmp_path, monkeypatch):
    """directory 를 직접 주면 그쪽이 우선이고, 기억은 그래도 갱신된다."""
    from custom_file_dialog import exec_file_dialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)
    first = tmp_path / "처음"
    forced = tmp_path / "강제"
    first.mkdir()
    forced.mkdir()
    (first / "a.csv").write_text("x")
    (forced / "b.csv").write_text("x")

    seen, result = _dialog_start_dirs(monkeypatch)
    result["paths"] = [str(first / "a.csv")]
    exec_file_dialog(mode="open_file", remember="용도")
    assert last_dir("용도") == str(first)

    # 기억이 있어도 directory 가 이긴다
    result["paths"] = [str(forced / "b.csv")]
    exec_file_dialog(mode="open_file", remember="용도", directory=str(forced))
    assert seen[-1] == str(forced)
    assert last_dir("용도") == str(forced)      # 기억은 갱신된다


def test_remember_falls_back_when_dir_is_gone(qapp, tmp_path, monkeypatch):
    """기억해 둔 폴더가 사라졌으면 안전한 곳으로 대체한다."""
    from custom_file_dialog import exec_file_dialog, remember_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    gone = tmp_path / "사라질폴더"
    gone.mkdir()
    remember_dir("용도", str(gone))
    gone.rmdir()

    seen, _result = _dialog_start_dirs(monkeypatch)
    exec_file_dialog(mode="open_file", remember="용도")
    assert seen[-1] == os.getcwd()


def test_remember_shares_store_with_widget(qapp, tmp_path, monkeypatch):
    """같은 이름이면 위젯(settings_key)과 다이얼로그(remember)가 기억을 주고받는다."""
    from custom_file_dialog import exec_file_dialog, last_dir, remember_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    from_dialog = tmp_path / "다이얼로그"
    from_widget = tmp_path / "위젯"
    from_dialog.mkdir()
    from_widget.mkdir()
    (from_dialog / "a.csv").write_text("x")
    (from_widget / "b.csv").write_text("x")

    # 다이얼로그가 고른 것을 위젯이 이어받는다
    _seen, result = _dialog_start_dirs(monkeypatch)
    result["paths"] = [str(from_dialog / "a.csv")]
    exec_file_dialog(mode="open_file", remember="공용")

    edit = FilePathEdit(mode="open_file", settings_key="공용")
    assert edit._start_dir_now() == str(from_dialog)

    # 반대로 위젯이 고른 것을 다이얼로그가 이어받는다
    remember_dir("공용", str(from_widget / "b.csv"))
    assert last_dir("공용") == str(from_widget)


def test_remember_dir_helpers(qapp, tmp_path, monkeypatch):
    """remember_dir 는 파일이면 그 파일이 있는 폴더를 기억한다."""
    from custom_file_dialog import last_dir, remember_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    target = tmp_path / "a.csv"
    target.write_text("x")

    assert remember_dir("k", str(target)) == str(tmp_path)      # 파일 -> 그 폴더
    assert last_dir("k") == str(tmp_path)
    assert remember_dir("k", str(tmp_path)) == str(tmp_path)    # 폴더 -> 그대로

    # 빈 값은 아무 일도 하지 않는다
    assert remember_dir("k", "") is None
    assert remember_dir("", str(target)) is None
    assert last_dir("") is None
    assert last_dir(None) is None
    assert last_dir("k") == str(tmp_path)                       # 그대로 남아 있다


def test_memory_history_without_key():
    """key 가 없으면 QSettings 를 건드리지 않고 메모리에만 남는다."""
    history = PathHistory(key=None, max_items=2)
    history.add("/a")
    history.add("/b")
    history.add("/c")
    assert history.items() == ["/c", "/b"]
