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
    assert suffix_of("모든 파일 (*)") is None       # 확장자를 특정할 수 없음
    assert ensure_suffix("/tmp/out", "csv") == "/tmp/out.csv"
    assert ensure_suffix("/tmp/out.json", "csv") == "/tmp/out.json"  # 이미 있으면 유지
    assert ensure_suffix("", "csv") == ""


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

