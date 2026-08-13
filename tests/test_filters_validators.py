"""필터 조립(build_filter)과 경로 유효성 판정."""

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
    assert suffix_of("압축 (*.tar.gz)") == "tar.gz"
    assert suffix_of("모든 파일 (*)") is None       # 확장자를 특정할 수 없음
    assert ensure_suffix("/tmp/out", "csv") == "/tmp/out.csv"
    assert ensure_suffix("/tmp/out.json", "csv") == "/tmp/out.json"  # 이미 있으면 유지
    assert ensure_suffix("", "csv") == ""


def test_suffix_ignores_non_extension_patterns():
    """접미사/접두사 패턴은 "붙여 줄 확장자"가 아니다.

    ``*lib`` 에서 "lib" 을 확장자로 오인하면 저장 모드에서 "foo" 가
    "foo.lib" 이, ``*_corner`` 에서는 "foo._corner" 가 된다 — 고른 필터에도
    안 걸리는 이름이다. 확장자 패턴(``*.ext``)일 때만 붙인다.
    """
    assert suffix_of("라이브러리 (*lib)") is None
    assert suffix_of("코너 (*_corner)") is None
    assert suffix_of("접두 (lib_*)") is None
    assert suffix_of("혼합 (*.c*)") is None                  # 와일드카드가 남은 확장자
    # 확장자 패턴이 함께 있으면 그것을 쓴다
    assert suffix_of("라이브러리 (*lib *.so)") == "so"


def test_build_filter_keeps_affix_patterns():
    """접미사/접두사 패턴이 조립 과정에서 확장자로 변형되지 않는다."""
    assert build_filter(
        [("라이브러리", ["*lib"]), ("코너", ["*_corner"]), ("접두", ["lib_*"])],
        add_all_files=False,
    ) == "라이브러리 (*lib);;코너 (*_corner);;접두 (lib_*)"


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



def test_dialog_lists_affix_patterns(qapp, tmp_path):
    """다이얼로그 목록이 접미사/접두사 패턴(*lib · *_corner · lib_*)을 거른다.

    확장자(*.txt)만 되는 게 아니라 임의 위치의 와일드카드가 끝까지(조립 →
    Qt 필터 문자열 → 파일 목록) 살아 있는지 통째로 잠근다.
    """
    from qtpy.QtWidgets import QListView

    from custom_file_dialog import CustomFileDialog

    for name in ("foolib", "toplib", "mylib.so", "abc_corner", "corner_abc",
                 "lib_first", "readme.txt"):
        (tmp_path / name).write_text("x")

    dialog = CustomFileDialog(
        None, mode="open_file", directory=str(tmp_path),
        filters=[("라이브러리", ["*lib"]), ("코너", ["*_corner"]),
                 ("접두", ["lib_*"]), ("텍스트", ["txt"])],
    )
    dialog.show()

    view = dialog.findChild(QListView, "listView")

    def visible():
        _spin(qapp, 700)
        model, root = view.model(), view.rootIndex()
        return sorted(
            model.index(r, 0, root).data() for r in range(model.rowCount(root))
        )

    expected = {
        "라이브러리 (*lib)": ["foolib", "toplib"],
        "코너 (*_corner)": ["abc_corner"],
        "접두 (lib_*)": ["lib_first"],
        "텍스트 (*.txt)": ["readme.txt"],
    }
    assert sorted(dialog.nameFilters()) == sorted(expected)
    for name_filter, files in expected.items():
        dialog.selectNameFilter(name_filter)
        assert visible() == files, name_filter
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)         # 지연 삭제를 여기서 소화해 다음 테스트에 안 넘긴다


def test_save_mode_keeps_affix_pattern_names(qapp, tmp_path):
    """저장 모드가 접미사 패턴 필터에서 엉뚱한 "확장자"를 붙이지 않는다.

    "*_corner" 필터로 "ss" 를 저장하면 예전에는 "ss._corner" 가 됐다 — 고른
    필터에도 안 걸리는 이름이다. 확장자 패턴(*.csv)만 붙인다.
    """
    from custom_file_dialog import CustomFileDialog

    dialog = CustomFileDialog(
        None, mode="save_file", directory=str(tmp_path),
        filters=[("코너", ["*_corner"]), ("CSV", ["csv"])],
    )
    dialog.selectNameFilter("코너 (*_corner)")
    dialog.selectFile("ss")
    assert dialog.selectedFiles() == [os.path.join(str(tmp_path), "ss")]

    dialog.selectNameFilter("CSV (*.csv)")
    assert dialog.selectedFiles() == [os.path.join(str(tmp_path), "ss.csv")]
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_directory_mode_rejects_existing_file(tmp_path):
    """폴더 자리에 이미 **파일**이 있으면 must_exist 와 무관하게 오류다."""
    target = tmp_path / "이름.txt"
    target.write_text("x")

    for must_exist in (True, False):
        ok, reason = validate_paths(
            [str(target)], mode=SelectMode.DIRECTORY, must_exist=must_exist
        )
        assert not ok and "폴더가 아니라 파일" in reason, must_exist

    # 아직 없는 폴더는 must_exist=False 에서 그대로 유효하다
    ok, _ = validate_paths(
        [str(tmp_path / "새폴더")], mode=SelectMode.DIRECTORY, must_exist=False
    )
    assert ok
