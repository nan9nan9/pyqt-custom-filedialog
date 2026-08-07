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
