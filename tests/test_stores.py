"""즐겨찾기(FavoritesStore) · 최근 파일(RecentStore) 저장소."""

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



def test_is_category_dir_skips_fs_for_outside_paths(store, tmp_path, monkeypatch):
    """저장소 밖 경로는 파일시스템을 만지지 않고 거른다.

    아이콘 제공자가 파일 목록의 항목마다 부르므로, 밖의 경로에 isdir 을 하면
    항목 수만큼 시스템 콜이 나가고 죽은 마운트에서는 그리다 멈춘다.
    """
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    calls = []
    real_isdir = os.path.isdir
    monkeypatch.setattr(
        os.path, "isdir", lambda p: (calls.append(p), real_isdir(p))[1]
    )

    assert not store.is_category_dir("/전혀/다른/곳/폴더")
    assert calls == []                       # 문자열 비교만으로 끝났다

    # 진짜 분류 폴더는 여전히 잡는다(이때만 isdir 을 만진다)
    assert store.is_category_dir(store.category_dir("설계"))
    assert calls
