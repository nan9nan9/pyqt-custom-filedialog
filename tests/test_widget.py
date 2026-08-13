"""FilePathEdit / FilePathForm 위젯 동작."""

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
    assert kwargs["filters"] == "텍스트 (*.txt);;모든 파일 (*)"
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



def test_read_only_toggle_keeps_clear_button_off(qapp):
    """clear_button=False 로 만든 위젯은 read_only 를 껐다 켜도 X 버튼이 없다."""
    edit = FilePathEdit(mode="open_file", clear_button=False)
    assert not edit.line_edit.isClearButtonEnabled()

    edit.set_read_only(True)
    edit.set_read_only(False)
    assert not edit.line_edit.isClearButtonEnabled()

    # 기본(clear_button=True)은 read_only 에서만 꺼졌다가 되살아난다
    normal = FilePathEdit(mode="open_file")
    normal.set_read_only(True)
    assert not normal.line_edit.isClearButtonEnabled()
    normal.set_read_only(False)
    assert normal.line_edit.isClearButtonEnabled()


def test_set_mode_updates_completer_filter(qapp):
    """모드를 바꾸면 자동완성 후보 범위(폴더만/전부)도 따라간다."""
    from qtpy.QtCore import QDir

    edit = FilePathEdit(mode="open_file")
    assert edit._completer_model.filter() & QDir.Filter.Files

    edit.set_mode("directory")
    assert not (edit._completer_model.filter() & QDir.Filter.Files)
    assert edit._completer_model.filter() & QDir.Filter.Dirs

    edit.set_mode("open_file")
    assert edit._completer_model.filter() & QDir.Filter.Files


def test_form_add_path_ignores_parent_kwarg(qapp):
    """add_path 에 parent 를 넘겨도 (폼이 부모이므로) 조용히 무시한다."""
    form = FilePathForm()
    edit = form.add_path("input", "입력:", mode="open_file", parent=None)
    assert edit.parent() is form


def test_fixed_sidebar_urls_forces_qt_dialog(qapp, tmp_path, monkeypatch):
    """보호 위치만 지정해도 Qt 자체 창으로 연다.

    그 지정을 지키려면 우리 우클릭 메뉴가 필요하고, 메뉴는 네이티브 창에
    걸 수 없다. 예전에는 네이티브로 열려 지정이 조용히 무시됐다.
    """
    used = []
    monkeypatch.setattr(
        dialog_module, "_run_dialog",
        lambda *a, **k: (used.append("native"), ([], ""))[1],
    )
    monkeypatch.setattr(
        dialog_module, "exec_dialog",
        lambda dialog: (used.append(type(dialog).__name__), 0)[1],
    )

    edit = FilePathEdit(mode="open_file", fixed_sidebar_urls=[str(tmp_path)])
    edit.browse()
    assert used == ["CustomFileDialog"], used


def test_paths_does_not_split_single_mode(qapp, tmp_path):
    """단일 선택에서는 구분자로 쪼개지 않는다 — ``a;b.txt`` 도 합법인 이름이다."""
    tricky = str(tmp_path / "a;b.txt")
    edit = FilePathEdit(mode="open_file")
    edit.set_path(tricky)
    assert edit.paths() == [tricky]
    assert edit.path() == tricky

    # 여러 개 모드에서는 예전처럼 구분자로 나눈다
    multi = FilePathEdit(mode="open_files")
    multi.set_paths(["/a.txt", "/b.txt"])
    assert multi.paths() == ["/a.txt", "/b.txt"]


def test_drag_drop_off_blocks_line_edit_drops(qapp):
    """드롭을 끄면 입력창도 Qt 기본 드롭을 받지 않는다.

    받으면 "file:///…" 원문이 그대로 붙어 경로가 아닌 값이 들어간다.
    """
    off = FilePathEdit(mode="open_file", drag_drop=False)
    assert not off.acceptDrops()
    assert not off.line_edit.acceptDrops()

    on = FilePathEdit(mode="open_file", drag_drop=True)
    assert on.acceptDrops() and not on.line_edit.acceptDrops()

    on.set_drag_drop_enabled(False)
    assert not on.acceptDrops() and not on.line_edit.acceptDrops()


def test_set_mode_trims_paths_when_leaving_multi(qapp):
    """여러 개 → 하나로 바꾸면 첫 경로만 남는다.

    paths() 가 모드에 따라 다르게 쪼개므로, 새 모드를 넣은 뒤에 읽으면 합쳐진
    텍스트가 통째로 한 경로가 되어 아무것도 잘리지 않았다.
    """
    edit = FilePathEdit(mode="open_files")
    edit.set_paths(["/tmp/a.txt", "/tmp/b.txt"])
    assert edit.paths() == ["/tmp/a.txt", "/tmp/b.txt"]

    edit.set_mode("open_file")
    assert edit.paths() == ["/tmp/a.txt"]
    assert edit.path() == "/tmp/a.txt"


def test_effective_sidebar_shows_a_folder_in_save_mode(qapp, tmp_path):
    """저장 모드에서도 "현재 위치" 는 **폴더**다(파일이 아니라).

    저장 모드의 시작 위치는 파일 경로다(다이얼로그가 이름을 미리 채우도록).
    그것을 그대로 사이드바 조회에 넘겨, 파일이 항목으로 나오고 "현재 위치"
    라는 이름까지 붙었다 — 실제 다이얼로그는 부모 폴더를 쓴다.
    """
    from custom_file_dialog import FavoritesStore

    target = tmp_path / "결과.csv"
    target.write_text("x")
    # 사이드바를 손대는 구성이라야 표시 목록이 나온다(저장소가 없으면 그대로 둔다)
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store.add("설계", str(target))

    edit = FilePathEdit(mode="save_file", favorites=store)
    edit.set_path(str(target))

    marks = edit.effective_sidebar_marks()
    assert os.path.normpath(str(tmp_path)) in marks         # 폴더가 "현재 위치"
    assert os.path.normpath(str(target)) not in marks       # 파일은 아니다

    urls = [u.toLocalFile() for u in edit.effective_sidebar_urls()]
    assert os.path.normpath(str(target)) not in [os.path.normpath(u) for u in urls]


def test_history_follows_settings_configured_later(qapp, tmp_path):
    """위젯을 만든 **뒤에** configure_settings 를 불러도 기억을 공유한다.

    예전에는 만들 때의 저장소에 값을 묶어 둬서, 나중에 공용 저장소를 지정하면
    그 위젯만 옛 저장소를 계속 썼다. 그것을 고치자 이번에는 목록을 고친 뒤에
    저장소를 확인해 **다시 읽기가 방금 넣은 값을 덮어썼다** — 즉 이미 쌓여
    있던 기록이 통째로 날아갔다.
    """
    from qtpy.QtCore import QSettings

    from custom_file_dialog import configure_settings
    from custom_file_dialog.history import PathHistory

    shared = QSettings("테스트조직", "테스트앱")
    shared.setValue("custom_file_dialog/키/recent", ["/data/a.csv", "/data/b.csv"])
    shared.setValue("custom_file_dialog/키/last_dir", "/data")
    shared.sync()

    history = PathHistory(key="키")          # 설정을 지정하기 **전에** 만든다
    configure_settings("테스트조직", "테스트앱")
    try:
        history.add("/data/new.csv")

        saved = QSettings("테스트조직", "테스트앱").value("custom_file_dialog/키/recent")
        assert saved[0] == "/data/new.csv"
        assert "/data/a.csv" in saved and "/data/b.csv" in saved   # 기존 기록 유지
        assert history.last_dir() == "/data"                       # 값도 새 저장소 기준
    finally:
        configure_settings(None, None)


def test_history_is_shared_between_instances(qapp):
    """같은 이름을 쓰면 **같은 기억을 공유한다** (모듈 설명의 약속).

    값을 인스턴스에 담아 두면 그 사이 남이 바꾼 것을 못 보고, 다음 저장이
    남의 기록을 통째로 덮어쓴다. 위젯 둘이 같은 settings_key 를 쓰거나
    위젯과 remember_dir 헬퍼가 섞일 때 실제로 그랬다.
    """
    from custom_file_dialog import configure_settings
    from custom_file_dialog.history import PathHistory, remember_dir

    configure_settings("공유테스트조직", "공유테스트앱")
    try:
        first = PathHistory(key="공유키")
        second = PathHistory(key="공유키")
        first.clear()

        first.add("/data/a.csv")
        first.set_last_dir("/data")
        assert second.items() == ["/data/a.csv"]        # 곧바로 보인다
        assert second.last_dir() == "/data"

        second.add("/other/b.csv")
        assert first.items() == ["/other/b.csv", "/data/a.csv"]   # 덮어쓰지 않는다

        # 헬퍼로 바꾼 것도 위젯 쪽에서 보인다
        remember_dir("공유키", "/etc/hosts")
        assert first.last_dir() == "/etc"

        # 목록 지우기가 실제로 비운다("▾ → 목록 지우기" 가 이 경로다)
        first.clear()
        assert second.items() == []
        assert second.last_dir() == "/etc"              # 마지막 폴더는 그대로
    finally:
        configure_settings(None, None)


def test_drop_never_invents_a_parent_path(tmp_path):
    """폴더 모드 드롭이 "확인 못 한 경로"를 부모로 바꿔치기하지 않는다.

    ``safe_isdir`` 는 죽은 원격·automount·차단 경로에서 판정을 못 하면 False 를
    준다. 그것을 "파일이구나"로 읽고 부모를 넣으면, 사용자가 떨어뜨린 적 없는
    폴더가 조용히 입력창에 들어가고 앱은 거기에 산출물을 쓴다.
    """
    from custom_file_dialog.drops import acceptable_paths

    folder = tmp_path / "결과"
    folder.mkdir()
    a_file = tmp_path / "a.csv"
    a_file.write_text("x")

    real_isdir = os.path.isdir
    real_isfile = os.path.isfile
    blocked = str(folder)

    def isdir(path):        # 차단된 자리는 "모르겠다" = False
        return False if path == blocked else real_isdir(path)

    def isfile(path):
        return False if path == blocked else real_isfile(path)

    # 확인 수단이 있으면 만들어 내지 않는다
    assert acceptable_paths([blocked], SelectMode.DIRECTORY, isdir, isfile) == []
    # 진짜 파일은 예전대로 부모 폴더로 받아 준다
    assert acceptable_paths(
        [str(a_file)], SelectMode.DIRECTORY, isdir, isfile
    ) == [str(tmp_path)]
    # 진짜 폴더는 그대로
    assert acceptable_paths(
        [str(folder)], SelectMode.DIRECTORY, real_isdir, real_isfile
    ) == [str(folder)]
    # isfile 을 안 주면 예전 동작(폴더가 아니면 파일)
    assert acceptable_paths([blocked], SelectMode.DIRECTORY, isdir) == [str(tmp_path)]


def test_drop_rejects_unverifiable_folder_in_file_mode(tmp_path):
    """파일 모드 드롭도 "확인 못 한 경로"를 받지 않는다.

    ``isdir`` 만 보면 automount 위·죽은 원격에서 폴더가 False 로 돌아와,
    폴더가 파일 칸에 그대로 들어갔다(폴더 모드와 기준이 갈리던 자리다).
    """
    from custom_file_dialog.drops import acceptable_paths

    folder = tmp_path / "myaccount"
    folder.mkdir()
    a_file = tmp_path / "a.csv"
    a_file.write_text("x")

    real_isdir, real_isfile = os.path.isdir, os.path.isfile
    blocked = str(folder)

    def isdir(path):        # 차단된 자리는 "모르겠다" = False
        return False if path == blocked else real_isdir(path)

    def isfile(path):
        return False if path == blocked else real_isfile(path)

    for mode in (SelectMode.OPEN_FILE, SelectMode.OPEN_FILES, SelectMode.SAVE_FILE):
        assert acceptable_paths([blocked], mode, isdir, isfile) == [], mode
        assert acceptable_paths([str(a_file)], mode, isdir, isfile) == [str(a_file)]
        # 로컬 폴더는 예전대로 거부
        assert acceptable_paths([str(tmp_path)], mode, isdir, isfile) == []
