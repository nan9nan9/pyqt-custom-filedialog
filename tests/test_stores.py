"""즐겨찾기(FavoritesStore) · 최근 파일(RecentStore) 저장소."""

import os
import shutil
import tempfile
import time

import pytest

from qtpy.QtCore import QDir, QMimeData, QPoint, QSettings, Qt, QUrl
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


def test_remove_category_survives_rmdir_failure(store, tmp_path, monkeypatch):
    """링크 하나가 안 지워져 rmdir 이 실패해도 예외가 튀어 오르지 않는다."""
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    def boom(path):
        raise OSError("장치 사용 중")

    monkeypatch.setattr(os, "rmdir", boom)
    store.remove_category("설계")            # 예외 없이 돌아와야 한다


def test_stores_survive_non_utf8_filenames(tmp_path):
    """비UTF-8(서러게이트) 파일명도 등록·기록·복원이 된다.

    옛 공유 폴더의 EUC-KR 파일명은 파이썬에 서러게이트 문자열로 들어온다.
    인덱스 저장(json -> utf-8)이 이를 인코딩 못 해 add()/record() 가
    UnicodeEncodeError 로 터졌다 — 우클릭 "즐겨찾기에 추가"와 다이얼로그
    accept(record_recent) 가 그런 파일에서 죽었다.
    """
    if os.name != "posix":
        pytest.skip("바이트 파일명은 POSIX 전용")

    raw = os.path.join(str(tmp_path).encode(), b"\xbb\xe9\xbc\xad.csv")
    open(raw, "wb").close()
    name = next(n for n in os.listdir(str(tmp_path)))
    path = os.path.join(str(tmp_path), name)
    assert any(0xDC80 <= ord(c) <= 0xDCFF for c in name)   # 정말 서러게이트인지

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    link = store.add("자료", path)                   # 터지지 않아야 한다
    assert store.resolve(link) == path

    # 새 인스턴스로 다시 읽어도(=인덱스 파일 왕복) 원본이 복원된다
    reloaded = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    assert reloaded.items("자료") == [path]

    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    assert recent.record(path)                       # 터지지 않아야 한다
    assert recent.items() == [path]


def test_safe_name_strips_windows_chars_on_nt(monkeypatch):
    """윈도우에서는 파일명 금지 문자도 바꾼다(리눅스 동작은 그대로)."""
    from custom_file_dialog.favorites import _safe_name

    assert _safe_name("시간:분") == "시간:분"          # 리눅스: 합법이라 유지

    monkeypatch.setattr(os, "name", "nt")
    assert _safe_name('시간:분*?"<>|') == "시간_분" + "_" * 6
    assert _safe_name("보통이름") == "보통이름"


def test_index_cache_sees_other_instances(tmp_path):
    """인덱스 캐시가 다른 인스턴스(프로세스)의 변경을 놓치지 않는다.

    _load_index 는 (mtime, 크기)가 같으면 캐시를 쓴다. 같은 폴더를 가리키는
    다른 저장소가 항목을 더하면 파일이 바뀌므로 다시 읽어야 하고, 돌려주는
    dict 은 사본이라 호출자가 고쳐도 캐시가 오염되면 안 된다.
    """
    base = str(tmp_path / "favorites")
    a = FavoritesStore(base_dir=base)
    b = FavoritesStore(base_dir=base)

    f1 = _touch(tmp_path, "하나.csv")
    f2 = _touch(tmp_path, "둘.csv")

    link1 = a.add("설계", f1)
    assert b.resolve(link1) == f1            # b 가 a 의 저장을 읽는다 (캐시 생성)

    link2 = b.add("설계", f2)                # b 쪽 변경
    assert a.resolve(link2) == f2            # a 캐시가 무효화되어 새로 읽는다

    # 호출자가 돌려받은 dict 을 고쳐도 캐시는 오염되지 않는다
    index = a._load_index()
    index.clear()
    assert a.resolve(link1) == f1


def test_configure_storage_moves_both_stores(tmp_path):
    """저장소 뿌리 하나로 즐겨찾기(<뿌리>/favorites)·최근(<뿌리>/recent)이 정해진다."""
    from custom_file_dialog import (
        configure_storage,
        default_base_dir,
        default_storage_dir,
    )
    from custom_file_dialog.recent import default_recent_dir

    root = str(tmp_path / "저장소")
    try:
        assert configure_storage(root) == os.path.normpath(root)
        assert default_storage_dir() == os.path.normpath(root)
        assert default_base_dir() == os.path.join(os.path.normpath(root), "favorites")
        assert default_recent_dir() == os.path.join(os.path.normpath(root), "recent")

        store = FavoritesStore()
        recent = RecentStore()
        assert store.base_dir == os.path.join(os.path.normpath(root), "favorites")
        assert recent.base_dir == os.path.join(os.path.normpath(root), "recent")

        # configure_favorites (즐겨찾기 폴더 직접 지정)가 뿌리보다 우선한다
        from custom_file_dialog import configure_favorites

        configure_favorites(str(tmp_path / "따로"))
        assert default_base_dir() == os.path.normpath(str(tmp_path / "따로"))
    finally:
        from custom_file_dialog import configure_favorites

        configure_favorites(None)
        configure_storage(None)


def test_default_storage_is_under_user_config():
    """지정이 없으면 기본은 ~/.config/custom_file_dialog (XDG_CONFIG_HOME 준수)다.

    예전 기본(QStandardPaths.AppDataLocation)은 앱 이름이 없는 환경에서 엉뚱한
    자리(임시 폴더 등)에 만들어질 수 있었다.
    """
    from custom_file_dialog import (
        configure_favorites,
        configure_storage,
        default_base_dir,
        default_storage_dir,
    )

    configure_favorites(None)
    configure_storage(None)

    expected_root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    assert default_storage_dir() == os.path.join(
        expected_root, "custom_file_dialog"
    )
    assert default_base_dir() == os.path.join(
        expected_root, "custom_file_dialog", "favorites"
    )


def _hang_stats_on(monkeypatch, prefix, seconds=3.0):
    """``prefix`` 아래 경로의 stat 이 돌아오지 않는 상황을 만든다(죽은 마운트)."""
    real_stat, real_lstat = os.stat, os.lstat

    def hang(func):
        def wrapper(path, *args, **kwargs):
            if str(path).startswith(prefix):
                time.sleep(seconds)
            return func(path, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(os, "stat", hang(real_stat))
    monkeypatch.setattr(os, "lstat", hang(real_lstat))


def _fake_remote_mount(monkeypatch, mountpoint):
    from custom_file_dialog import safety


    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (mountpoint, "nfs4", "srv:/export")],
    )
    monkeypatch.setattr(safety_reach, "probe_host", lambda *a, **k: True)
    safety.configure(timeout=0.2)


def test_bookkeeping_survives_dead_mount(tmp_path, monkeypatch):
    """확정 뒤 뒷정리(최근 기록 · 즐겨찾기 등록 · 마지막 폴더)가 죽은 마운트에서
    GUI 를 멈추지 않는다.

    이 함수들은 **사용자가 고른 경로**를 그대로 만진다. 평범한 os.path.isfile /
    exists / isdir 로 확인하면 죽은 NFS 에서 영영 돌아오지 않아, 파일을 고르고
    확인을 누른 순간 앱이 통째로 멎는다.
    """
    from custom_file_dialog import history, safety

    dead = str(tmp_path / "nfs")
    os.makedirs(dead)
    victim = os.path.join(dead, "보고서.csv")
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("x")

    _fake_remote_mount(monkeypatch, dead)
    _hang_stats_on(monkeypatch, dead)
    try:
        store = FavoritesStore(base_dir=str(tmp_path / "fav"))
        recent = RecentStore(base_dir=str(tmp_path / "rec"))

        start = time.time()
        assert recent.record(victim) is None          # 기록은 건너뛰고
        with pytest.raises(FavoritesError):           # 등록은 실패로 알린다
            store.add("설계", victim)
        assert history.remember_dir("키", victim, settings=QSettings()) == dead
        assert time.time() - start < 2.5              # 3초짜리 stat 을 안 기다렸다
    finally:
        safety.clear_cache()
        safety.reset()


def test_resolve_never_follows_dead_target(tmp_path, monkeypatch):
    """링크 복원이 **대상을 stat 하지 않는다** — 원본이 죽은 마운트여도 즉시.

    os.path.realpath 는 대상을 따라가며 stat 하므로, 목록을 그리거나 고른
    경로를 복원하는 것만으로 멈췄다.
    """
    dead = str(tmp_path / "nfs")
    os.makedirs(dead)
    target = os.path.join(dead, "설계도.csv")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("x")

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    link = store.add("설계", target)                  # 아직 살아 있을 때 등록
    inner_target = os.path.join(dead, "산출물")
    os.makedirs(inner_target)
    folder_link = store.add("설계", inner_target)

    # 인덱스를 지워 링크만 보고 풀어야 하는 상황으로 만든다(옛 저장소 등)
    index_path = os.path.join(store.base_dir, ".index.json")
    os.remove(index_path)
    store._index_cache = None

    _hang_stats_on(monkeypatch, dead)
    start = time.time()
    assert store.resolve(link) == target
    assert store.resolve(folder_link) == inner_target
    # 링크 폴더 **안쪽** 경로도 대상을 만지지 않고 풀린다
    assert store.resolve(os.path.join(folder_link, "안쪽", "a.csv")) == os.path.join(
        inner_target, "안쪽", "a.csv"
    )
    assert time.time() - start < 2.5


def test_favorites_reject_store_internal_paths(tmp_path):
    """저장소 안 항목은 즐겨찾기에 등록되지 않는다(링크 창고 순환 방지).

    저장소 자신·분류 폴더·분류 안의 링크를 등록하면 "링크를 가리키는 링크"가
    생겨, 들어가도 원본이 아니라 창고를 헤매게 된다.
    """
    design = _touch(tmp_path, "설계도.csv")
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    link = store.add("설계", design)                 # 정상 등록은 그대로 된다
    category = store.category_dir("설계")

    for inside in (store.base_dir, category, link):
        with pytest.raises(FavoritesError):
            store.add("보고서", inside)
    assert store.categories() == ["설계"]            # 분류가 새로 생기지도 않는다


def test_icon_provider_is_never_swapped(qapp, tmp_path):
    """아이콘 제공자를 **폴더를 옮길 때마다 갈아 끼우지 않는다.**

    ``setIconProvider`` 는 모델이 그때까지 기억한 **모든 노드**를 다시 훑으며
    노드마다 QFileInfo 를 만든다(= stat). 실측: 폴더 40개를 둘러본 뒤 교체
    한 번에 icon() 1,751회 · 18.5ms(로컬 ext4). 네트워크 홈에서는 그 stat 이
    전부 서버 왕복이라 교체 한 번이 초 단위가 되고, 저장소를 드나들 때마다
    그 값을 문다 — 즐겨찾기·최근 파일을 눌렀을 때 가장 느렸던 이유다.

    한 번만 걸어 두면 항목마다 도는 파이썬 판정이 남지만, 그것은 문자열 비교
    하나라 나열 한 번의 비용일 뿐이고 **오갈 때마다 늘지 않는다.**
    """
    from qtpy.QtWidgets import QListView

    from custom_file_dialog import CustomFileDialog
    from custom_file_dialog.icons import CategoryIconProvider

    design, _report, _output = _make_tree(tmp_path)
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store.add("설계", design)

    class Counting(CustomFileDialog):
        """setIconProvider 가 **몇 번** 불리는지 센다.

        같은 제공자를 다시 걸어도 비용은 그대로 든다(모델이 노드 트리를 다시
        훑는다). 그래서 "무엇이 걸렸나"가 아니라 **몇 번 걸었나**를 본다.
        """

        def __init__(self, *args, **kwargs):
            self.installs = 0
            super().__init__(*args, **kwargs)

        def setIconProvider(self, provider):      # noqa: N802 (Qt 시그니처)
            self.installs += 1
            super().setIconProvider(provider)

    dialog = Counting(
        None, mode="open_file", directory=os.path.dirname(design), favorites=store
    )
    dialog.show()
    _spin(qapp, 300)

    provider = dialog.iconProvider()
    assert isinstance(provider, CategoryIconProvider)
    assert dialog.installs == 1                       # 만들 때 한 번뿐

    for where in (store.base_dir, str(tmp_path), store.category_dir("설계"),
                  os.path.dirname(design)):
        dialog.setDirectory(where)
        dialog.directoryEntered.emit(where)           # 사용자 이동과 같은 신호
        assert dialog.iconProvider() is provider, where
    assert dialog.installs == 1, "폴더를 옮길 때 제공자를 다시 걸었다"

    # 사이드바의 분류 아이콘은 제공자와 **무관하게** 유지된다 — 델리게이트가
    # 그리기 직전에 씌우기 때문이다(제공자에 맡기면 QUrlModel 이 파일시스템
    # 통지를 받을 때마다 경로에서 다시 읽어 폴더 아이콘으로 되돌아간다)
    from qtpy.QtWidgets import QStyleOptionViewItem

    sidebar = dialog.findChild(QListView, "sidebar")
    delegate = sidebar.itemDelegate()
    model = sidebar.model()
    drawn = {}
    for row in range(model.rowCount()):
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, model.index(row, 0))
        drawn[option.text] = option.icon
    star = store.category_dir("설계")
    assert drawn.get("설계") is not None
    assert drawn["설계"].availableSizes() == (
        places_module.Places(favorites=store).category_icon(store).availableSizes()
    ), star
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_unlink_never_follows_dead_link(tmp_path, monkeypatch):
    """죽은 원본을 가리키는 링크를 지울 때 대상을 stat 하지 않는다.

    os.path.isdir 를 먼저 보면 링크를 따라가며 대상을 stat 한다 — 원본이 죽은
    NFS 위면 "'설계'에서 제거"·최근 파일 재기록·정리에서 GUI 가 멈춘다.
    """
    from custom_file_dialog import safety

    dead = str(tmp_path / "nfs")
    os.makedirs(dead)
    target = os.path.join(dead, "설계도.csv")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("x")

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    link = store.add("설계", target)                  # 살아 있을 때 등록

    _hang_stats_on(monkeypatch, dead)
    try:
        start = time.time()
        assert store.remove("설계", "설계도.csv")      # 링크만 지운다
        assert time.time() - start < 2.5              # 대상을 만지지 않았다
        assert not os.path.lexists(link)
    finally:
        safety.clear_cache()


def test_true_means_default_store_everywhere(tmp_path, monkeypatch):
    """``True`` 는 어느 입구로 들어와도 "기본 위치에 만들어 쓴다" 는 뜻이다.

    예전에는 위젯이 ``favorites=True`` 를 정규화 없이 Places 로 넘겨,
    stores() 에 bool 이 섞이고 첫 질의에서 AttributeError 로 터졌다.
    """
    from custom_file_dialog import FilePathEdit, Places, configure_storage

    configure_storage(str(tmp_path / "저장소"))
    try:
        # 1) 저층(Places)에 직접 줘도 안전하다
        places = Places(favorites=True, recent=True)
        assert isinstance(places.favorites, FavoritesStore)
        assert isinstance(places.recent, RecentStore)
        assert places.is_inside("/tmp/없는경로") is False      # 질의가 터지지 않는다

        # 2) 위젯을 통해서도 같다
        edit = FilePathEdit(mode="open_file", favorites=True, recent_files=True)
        assert isinstance(edit.favorites(), FavoritesStore)
        assert isinstance(edit.recent_files(), RecentStore)
        assert edit._places().is_inside("/tmp/없는경로") is False
    finally:
        configure_storage(None)


def test_record_recent_never_escapes_into_slots(tmp_path, monkeypatch):
    """최근 기록이 실패해도 예외가 밖으로 새지 않는다.

    부르는 쪽은 둘 다 Qt 슬롯이다(다이얼로그 accepted · 위젯 찾아보기).
    슬롯에서 예외가 새면 PyQt 는 앱을 abort 시킨다 — 링크를 못 만들었다고
    파일 선택 자체가 무산되면 안 된다.
    """
    from custom_file_dialog import Places

    target = _touch(tmp_path, "고른파일.csv")
    recent = RecentStore(base_dir=str(tmp_path / "recent"))
    places = Places(recent=recent)

    def deny_link(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "symlink", deny_link)
    monkeypatch.setattr(os, "link", deny_link)

    places.record_recent([target])                  # 예외 없이 지나간다
    assert recent.items() == []                     # 기록만 안 될 뿐

    # 저장소 폴더를 아예 못 만드는 경우도 마찬가지
    monkeypatch.setattr(os, "makedirs", deny_link)
    places.record_recent([target])


def test_places_options_update_is_all_or_nothing(tmp_path):
    """모르는 이름이 섞이면 아무것도 바꾸지 않는다(캐시도 어긋나지 않는다)."""
    from custom_file_dialog.places import PlacesOptions

    options = PlacesOptions(icon=True)
    before = options.places()

    with pytest.raises(TypeError):
        options.update(icon=False, favorite=None)   # favorites 오타

    assert options.icon is True                     # 절반만 반영되지 않는다
    assert options.places() is before               # 캐시도 그대로다

    options.update(icon=False)                      # 정상 경로는 그대로 동작
    assert options.icon is False and options.places() is not before


def test_remove_by_name_does_not_match_cwd_paths(tmp_path, monkeypatch):
    """이름으로 지울 때 현재 작업 디렉터리 기준 경로로 오해하지 않는다.

    abspath("보고서.md") 는 cwd 기준 절대 경로가 되어, 우연히 그곳을 가리키는
    다른 이름의 링크까지 함께 지워졌다.
    """
    work = tmp_path / "작업"
    work.mkdir()
    victim = work / "보고서.md"
    victim.write_text("x")
    other = _touch(tmp_path, "다른.md")

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store.add("설계", str(other), name="보고서.md")     # 이름만 같은 항목
    store.add("설계", str(victim), name="원본연결")      # cwd 기준 경로와 겹치는 대상

    monkeypatch.chdir(work)                              # cwd 를 그 폴더로
    assert store.remove("설계", "보고서.md")             # 이름으로 하나만 지운다

    남은 = dict(store.entries("설계"))
    assert "원본연결" in 남은 and 남은["원본연결"] == str(victim)


def test_remove_accepts_relative_path_too(tmp_path, monkeypatch):
    """이름으로 못 찾으면 상대 경로로도 지울 수 있다(add 와 대칭)."""
    work = tmp_path / "작업"
    work.mkdir()
    target = work / "a.csv"
    target.write_text("x")

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    monkeypatch.chdir(tmp_path)
    store.add("설계", "작업/a.csv", name="보고서")      # 상대 경로로 등록
    assert store.items("설계") == [str(target)]

    assert store.remove("설계", "작업/a.csv")            # 상대 경로로 제거
    assert store.items("설계") == []


def test_display_name_cannot_escape_the_store(tmp_path):
    """표시 이름으로 저장소 **밖에** 링크를 만들 수 없다.

    name 은 공개 인자인데 손질 없이 경로로 쓰여, "../../x" 같은 값이 그대로
    상위 폴더에 링크를 만들었다. 그렇게 생긴 것은 저장소 밖이라 복원도
    제거도 되지 않는다(목록에도 안 잡힌다).
    """
    target = _touch(tmp_path, "원본.csv")
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))

    link = store.add("설계", target, name="../../탈출.txt")
    assert store.is_inside(link), link                     # 저장소 안에 있다
    assert not os.path.lexists(str(tmp_path / "탈출.txt"))  # 밖으로 새지 않았다
    assert store.items("설계") == [target]                  # 목록에도 제대로 잡힌다

    # 구분자가 든 이름은 손질되어 한 항목이 된다(예외로 죽지 않는다)
    name = os.path.basename(link)
    assert os.sep not in name and "/" not in name


def test_recent_dir_stays_under_storage_root(tmp_path):
    """즐겨찾기만 옮겨도 최근 파일 폴더가 홈으로 새지 않는다."""
    from custom_file_dialog import configure_favorites, configure_storage
    from custom_file_dialog.recent import default_recent_dir

    try:
        configure_storage(str(tmp_path / "뿌리"))
        configure_favorites(str(tmp_path / "즐겨찾기따로"))     # 즐겨찾기만 이동
        assert default_recent_dir() == os.path.join(
            os.path.normpath(str(tmp_path / "뿌리")), "recent"
        )
        assert RecentStore().base_dir == default_recent_dir()
    finally:
        configure_favorites(None)
        configure_storage(None)


def test_record_all_keeps_going_after_a_bad_name(tmp_path):
    """중간에 이름이 이상한 파일이 있어도 나머지는 기록된다.

    표시 이름 손질이 예외를 던지면서, 그 지점 뒤의 파일들이 통째로 빠졌다
    (게다가 record 는 remove 를 먼저 하므로 예전 항목까지 사라졌다).
    """
    first = _touch(tmp_path, "a.csv")
    odd = tmp_path / "   "                       # 공백뿐인 이름 — 리눅스에서 합법
    odd.write_text("x")
    last = _touch(tmp_path, "b.csv")

    recent = RecentStore(base_dir=str(tmp_path / "recent"))
    links = recent.record_all([first, str(odd), last])
    assert len(links) == 3, links
    assert set(recent.items()) == {first, str(odd), last}


def test_renamed_link_resolves_to_its_own_target(tmp_path):
    """링크 이름을 바꿔도 **그 링크가 가리키는** 원본으로 풀린다.

    인덱스를 무조건 먼저 믿으면, 이름을 서로 맞바꿨을 때 낡은 매핑이 이겨
    다른 항목의 원본을 돌려준다 — 저장 모드면 엉뚱한 파일을 덮어쓴다.
    """
    first = _touch(tmp_path, "하나.csv")
    second = _touch(tmp_path, "둘.csv")
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    link_one = store.add("설계", first, name="x.csv")
    store.add("설계", second, name="z.csv")

    # 우클릭 "이름 변경"이 하는 일 — 링크 파일 이름만 바꾼다
    renamed = os.path.join(os.path.dirname(link_one), "새이름.csv")
    os.rename(link_one, renamed)

    assert store.resolve(renamed) == first          # 자기 원본으로 풀린다
    assert sorted(store.items("설계")) == sorted([first, second])


def test_index_survives_interrupted_save(tmp_path, monkeypatch):
    """저장이 중간에 끊겨도 기존 매핑이 날아가지 않는다.

    제자리에서 잘라 쓰면 깨진 JSON 이 남고, 읽기는 그것을 {} 로 보고, 다음
    저장이 그 위에 한 항목만 얹어 나머지를 영구히 지웠다.
    """
    first = _touch(tmp_path, "하나.csv")
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store.add("설계", first)
    index_path = os.path.join(store.base_dir, ".index.json")
    before = open(index_path, encoding="utf-8").read()

    real_replace = os.replace

    def fail_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail_replace)
    second = _touch(tmp_path, "둘.csv")
    try:
        store.add("설계", second)
    except (OSError, FavoritesError):
        pass
    monkeypatch.setattr(os, "replace", real_replace)

    # 인덱스 파일은 예전 내용 그대로다(깨지지 않았다)
    assert open(index_path, encoding="utf-8").read() == before
    assert store.resolve(store.link_for("설계", first)) == first


def test_places_options_keeps_recent_max_on_explicit_none(tmp_path):
    """``recent_max=None`` 을 명시해도 기억해 둔 개수를 잃지 않는다.

    하나뿐인 호출자(``FilePathEdit.set_recent_files``)가 이 인자를 늘 함께
    넘기므로, 기본값만 보던 예전 pop 은 매번 None 을 돌려주었다 — 저장소만
    다시 지정할 때마다 20개짜리가 새로 생겨 21번째부터 즉시 지워졌다.
    """
    from custom_file_dialog import configure_storage
    from custom_file_dialog.places import PlacesOptions

    configure_storage(str(tmp_path / "저장소"))
    try:
        options = PlacesOptions(recent=True, recent_max=100)
        options.update(recent=True, recent_max=None)
        assert options.recent_max == 100
        assert options.places().recent.max_items == 100

        options.update(recent_max=5)                # 값을 주면 그대로 바뀐다
        assert options.recent_max == 5
        assert options.places().recent.max_items == 5
    finally:
        configure_storage(None)


def test_recent_max_never_trims_an_app_owned_store(tmp_path):
    """앱이 직접 넘긴 최근 저장소는 **개수를 건드리지 않는다.**

    ``recent_max`` 는 "우리가 만들 때 쓸 개수"인데 그 값을 남의 인스턴스에도
    밀어 넣으면 ``set_max_items`` -> ``_evict`` 가 링크를 실제로 지운다.
    즐겨찾기를 켠 것만으로 공유 목록의 항목이 디스크에서 사라졌다.
    """
    from custom_file_dialog import configure_storage
    from custom_file_dialog.places import PlacesOptions

    configure_storage(str(tmp_path / "저장소"))
    try:
        shared = RecentStore(base_dir=str(tmp_path / "공유"), max_items=50)
        for index in range(12):
            path = tmp_path / ("f%02d.csv" % index)
            path.write_text("x")
            shared.record(str(path))
        assert len(shared.items()) == 12

        options = PlacesOptions(recent=True, recent_max=3)
        options.update(recent=shared)               # 공유 목록으로 교체
        options.update(favorites=True)              # 즐겨찾기만 켠다
        assert shared.max_items == 50
        assert len(shared.items()) == 12            # 9개가 사라지지 않는다

        # 우리가 만든 저장소에는 그대로 반영된다
        options.update(recent=True, recent_max=4)
        options.update(icon=False)
        assert options.recent.max_items == 4
        options.update(recent_max=2)
        assert options.recent.max_items == 2
    finally:
        configure_storage(None)


def test_category_names_never_leak_os_errors(tmp_path):
    """분류·표시 이름 검증이 저수준 예외로 새지 않는다.

    널 바이트는 ``ValueError("embedded null byte")``, 아주 긴 이름은
    ``OSError(ENAMETOOLONG)`` 로 폴더를 만드는 순간 튀어나왔다 — 이 함수가
    약속한 "다듬거나 ValueError" 밖의 예외를 호출자가 받는다.
    """
    from custom_file_dialog import favorites as favorites_module

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    target = tmp_path / "설계도.csv"
    target.write_text("x")

    # 판정은 **파일시스템을 만지기 전에** 끝나야 한다 — 저수준 ValueError 나
    # OSError 가 대신 나면 호출자가 받는 예외 종류가 이름마다 달라진다.
    for bad in ("널\0바이트", "가" * 500, "a" * 5000):
        with pytest.raises(ValueError) as caught:
            favorites_module._safe_name(bad)
        assert "분류 이름" in str(caught.value), bad
        with pytest.raises(ValueError):
            store.add_category(bad)
    assert store.categories() == []          # 아무것도 만들어지지 않았다

    # 표시 이름은 예외를 던지지 않고 줄여서 받아 준다(확장자는 살린다)
    link = store.add("설계", str(target), name="가" * 500 + ".csv")
    assert os.path.basename(link).endswith(".csv")
    assert (
        len(os.path.basename(link).encode("utf-8"))
        <= favorites_module._LINK_NAME_BYTES
    )
    assert store.resolve(link) == str(target)

    long_source = tmp_path / ("나" * 80 + ".csv")
    long_source.write_text("y")
    link = store.add("설계", str(long_source))         # 이름을 자동으로 따온다
    assert store.resolve(link) == str(long_source)


def test_unverifiable_target_says_so(tmp_path):
    """확인할 수 없는 자리를 등록하면 "존재하지 않습니다"라고 하지 않는다."""
    from custom_file_dialog import safety

    root = tmp_path / "user"
    (root / "myaccount" / "proj").mkdir(parents=True)
    store = FavoritesStore(base_dir=str(tmp_path / "fav"))

    safety.configure(guarded_roots=[str(root)], min_depth=0)
    try:
        with pytest.raises(FavoritesError) as caught:
            store.add("설계", str(root))
        assert "존재하지 않습니다" not in str(caught.value)
        assert "확인할 수 없는" in str(caught.value)

        # 진짜 없는 경로는 예전 문구 그대로
        with pytest.raises(FavoritesError) as caught:
            store.add("설계", str(tmp_path / "없는파일.csv"))
        assert "존재하지 않습니다" in str(caught.value)
    finally:
        safety.reset()


def test_existing_long_category_stays_readable(tmp_path):
    """예전 판으로 만들어 둔 **긴 분류 폴더**를 계속 읽을 수 있다.

    ``_safe_name`` 은 만들 때만이 아니라 ``category_dir`` 을 거치는 **모든
    조회**(사이드바·메뉴·items·contains)가 지나는 길목이다. 파일시스템이
    받아 주는 이름을 여기서 더 좁게 거절하면, 이미 있는 분류를 조회조차 못
    하게 되고 우클릭 메뉴 슬롯에서 터져 앱이 죽는다.
    """
    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    target = tmp_path / "설계도.csv"
    target.write_text("x")

    name = "설계" + "가" * 70                  # 216바이트 — ext4 한도(255) 안
    assert 200 < len(name.encode("utf-8")) <= 255
    store.add_category(name)
    link = store.add(name, str(target))

    assert name in store.categories()
    assert store.items(name) == [str(target)]
    assert store.contains(name, str(target))
    assert store.resolve(link) == str(target)
    assert len(store.sidebar_urls()) == 1

    # 파일시스템이 못 받는 길이는 여전히 ValueError 로 막는다
    with pytest.raises(ValueError):
        store.add_category("가" * 300)


def test_record_recent_resolves_links_from_other_stores(tmp_path):
    """``Places.record_recent`` 이 즐겨찾기 링크를 **원본으로 풀어서** 기록한다.

    풀지 않으면 최근 목록이 링크 창고 경로를 들고 있게 되어, 즐겨찾기에서
    그 항목을 빼는 순간 최근 목록의 그 항목이 끊긴 링크가 된다.
    """
    favorites = FavoritesStore(base_dir=str(tmp_path / "fav"))
    recent = RecentStore(base_dir=str(tmp_path / "rec"), max_items=10)
    places = Places(favorites=favorites, recent=recent)

    target = tmp_path / "설계도.csv"
    target.write_text("x")
    link = favorites.add("설계", str(target))

    places.record_recent([link])
    assert recent.items() == [str(target)]

    # 즐겨찾기에서 빼도 최근 항목은 살아 있다
    favorites.remove("설계", str(target))
    assert recent.items() == [str(target)]
    assert os.path.exists(recent.items()[0])


def test_icon_provider_does_not_restat_what_qt_knows(qapp, tmp_path, monkeypatch):
    """아이콘 제공자가 **Qt 가 이미 아는 것**을 다시 stat 하지 않는다.

    Qt 는 항목을 그리려고 이미 stat 해서 채워 둔 QFileInfo 를 들고 우리를
    부른다. 거기서 또 os.path.isdir 을 하면 네트워크 저장소에서는 항목마다
    왕복이 한 번 더 생기고, 그게 그대로 목록 지연이 된다.
    """
    from qtpy.QtCore import QFileInfo

    from custom_file_dialog.icons import CategoryIconProvider

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    target = tmp_path / "설계도.csv"
    target.write_text("x")
    store.add("설계", str(target))
    category = store.category_dir("설계")

    provider = CategoryIconProvider(store)

    touched = []
    real_isdir = os.path.isdir
    monkeypatch.setattr(
        os.path, "isdir", lambda p: (touched.append(p), real_isdir(p))[1]
    )

    # 분류 폴더 — 전용 아이콘이 나오고, 그 판정에 stat 을 쓰지 않는다
    icon = provider.icon(QFileInfo(category))
    assert not icon.isNull()
    assert touched == [], "Qt 가 아는 답을 두고 다시 stat 했다: %s" % touched

    # 저장소 밖 항목도 그대로 (여기는 원래 파일시스템을 안 만진다)
    provider.icon(QFileInfo(str(target)))
    assert touched == []

    # 저장소가 직접 물으면(폴더인지 모르는 호출자) 예전대로 확인한다
    assert store.is_category_dir(category) is True
    assert touched == [category]


def test_icon_provider_asks_qt_once_per_kind(qapp, tmp_path, monkeypatch):
    """Qt 기본 아이콘은 **종류마다 한 번만** 묻는다.

    Qt 는 아이콘을 고르려고 종류를 알아내고 아이콘 테마 폴더(``~/.icons`` ·
    ``~/.local/share/icons``)를 뒤진다. 홈이 네트워크에 있으면 그 조회가 항목
    하나하나마다 서버 왕복이 된다 — 실측으로 항목당 4.9ms, 홈 274개에 1.3초가
    나왔다. 게다가 Qt 는 **화면에 안 보이는 항목까지 전부** 훑는다(필터로 7개만
    보이는 폴더에서도 274번 불렸다).
    """
    from qtpy.QtCore import QFileInfo
    from qtpy.QtWidgets import QFileIconProvider

    from custom_file_dialog import icons as icons_module
    from custom_file_dialog.icons import CategoryIconProvider

    # 캐시는 **프로세스에 하나**다(다이얼로그마다 새로 만들면 PySide 에서
    # 쌓인다). "종류마다 한 번만 묻는다"를 재려면 비운 상태에서 시작해야 한다.
    monkeypatch.setattr(icons_module, "_plain_icons", {})

    names = []
    for index in range(5):
        names.append(".설정%d" % index)            # 점파일 — 확장자 없음으로 본다
        names.append("실행파일%d" % index)          # 확장자 없음
        names.append("자료%d.csv" % index)
        names.append("자료%d.png" % index)
    for index in range(5):
        (tmp_path / ("폴더%d" % index)).mkdir()
        names.append("폴더%d" % index)
    for name in names:
        path = tmp_path / name
        if not path.exists():
            path.write_text("x")

    asked = []
    real = QFileIconProvider.icon
    monkeypatch.setattr(
        QFileIconProvider,
        "icon",
        lambda self, arg: (asked.append(arg), real(self, arg))[1],
    )

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    provider = CategoryIconProvider(store)
    for name in names:
        provider.icon(QFileInfo(str(tmp_path / name)))

    # 종류는 넷뿐이다: 평범한 폴더 · 확장자 없음(점파일 포함) · csv · png
    assert len(asked) == 4, [
        a.fileName() for a in asked if isinstance(a, QFileInfo)
    ]
    assert len(names) == 25                         # 항목은 25개인데 4번만 물었다

    # 종류가 다르면 아이콘도 다르다 — 캐시가 전부를 뭉뚱그리지 않는다
    folder = provider.icon(QFileInfo(str(tmp_path / "폴더0")))
    csv = provider.icon(QFileInfo(str(tmp_path / "자료0.csv")))
    assert folder.cacheKey() != csv.cacheKey()
    # 같은 종류면 **같은 아이콘 객체**를 그대로 준다
    assert provider.icon(QFileInfo(str(tmp_path / "자료1.csv"))).cacheKey() == (
        csv.cacheKey()
    )


def test_icon_cache_keeps_symlinks_distinct(qapp, tmp_path):
    """종류별 캐시가 **심볼릭 링크 표시를 지우지 않는다.**

    Qt 는 링크에 다른 아이콘을 준다. 즐겨찾기·최근 파일 폴더는 안이 전부
    링크라, 열쇠에서 링크 여부를 빼먹으면 하필 이 라이브러리가 만드는 화면에서
    가장 잘 보인다(분류 안의 링크가 실제 파일처럼 보인다).
    """
    from qtpy.QtCore import QFileInfo
    from qtpy.QtWidgets import QFileIconProvider

    from custom_file_dialog.icons import CategoryIconProvider

    real_dir = tmp_path / "실폴더"
    real_dir.mkdir()
    real_file = tmp_path / "실파일.csv"
    real_file.write_text("x")
    os.symlink(str(real_dir), str(tmp_path / "폴더링크"))
    os.symlink(str(real_file), str(tmp_path / "파일링크.csv"))

    def bitmap(provider, name):
        return provider.icon(QFileInfo(str(tmp_path / name))).pixmap(16, 16).toImage()

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    ours = CategoryIconProvider(store)
    plain = QFileIconProvider()

    for real, link in (("실폴더", "폴더링크"), ("실파일.csv", "파일링크.csv")):
        # 우리 판정이 Qt 기본과 같은 답을 준다 (다르면 링크 표시가 사라진 것)
        assert (bitmap(plain, real) == bitmap(plain, link)) is False, real
        assert (bitmap(ours, real) == bitmap(ours, link)) is False, real

    # 같은 종류끼리는 여전히 재사용한다 (캐시가 죽지 않았다)
    (tmp_path / "다른.csv").write_text("y")
    assert bitmap(ours, "실파일.csv") == bitmap(ours, "다른.csv")


def _icon_blob(icon):
    """아이콘을 바이트로 — 바인딩마다 다른 QImage 내부 접근을 피한다."""
    from qtpy.QtCore import QBuffer, QByteArray, QIODevice

    pixmap = icon.pixmap(16, 16)
    if pixmap.isNull():
        return b"NULL"
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def test_icon_cache_matches_qt_for_every_kind(qapp, tmp_path, monkeypatch):
    """종류별 캐시가 **어떤 순서로 물어도** Qt 기본과 같은 답을 준다.

    Qt 는 뿌리(디스크) · 폴더 · 파일 · 그 밖(FIFO·소켓 — 빈 아이콘)을 다르게
    본다. 그것을 한 열쇠로 뭉뚱그리면 **먼저 물어본 것이 뒤엣것을 덮어쓴다.**

    실제로 났던 일: 뿌리 ``/`` 를 폴더와 같은 열쇠로 묶었더니, 모델이 시작
    폴더의 인덱스를 만들며 루트부터 묻는 바람에 **모든 폴더가 하드디스크
    모양**이 됐다. FIFO·소켓을 확장자 없는 파일과 묶었을 때는 ``README`` 같은
    평범한 파일이 아이콘 없이 그려졌다.
    """
    from qtpy.QtCore import QFileInfo
    from qtpy.QtWidgets import QFileIconProvider

    from custom_file_dialog import icons as icons_module
    from custom_file_dialog.icons import CategoryIconProvider

    # 캐시는 **프로세스에 하나**라 앞 테스트가 채워 둔 것이 그대로 남는다.
    # 그것을 물려받으면 이 테스트가 재는 것이 달라진다 — 아래에서 "지금 Qt 가
    # 주는 답"과 비교하는데, **Qt6 + gtk3 는 폴더 아이콘 답이 프로세스마다
    # 갈린다**(같은 경로에 평범한 폴더 그림과 테마 폴더 그림이 번갈아 나온다.
    # GTK 아이콘 테마가 언제 다 읽히느냐에 달린 것으로 보인다). 그러면 앞
    # 테스트가 넣어 둔 값과 지금 값이 달라 **열쇠 설계와 무관하게** 깨진다 —
    # 실제로 이 테스트는 혼자 돌리면 통과하고 스위트에서는 PyQt6·PySide6 에서만
    # 깨졌다. 비워 놓고 시작한다.
    monkeypatch.setattr(icons_module, "_plain_icons", {})

    (tmp_path / "폴더A").mkdir()
    (tmp_path / "메모.txt").write_text("x")
    (tmp_path / "README").write_text("x")            # 확장자 없는 평범한 파일
    os.mkfifo(str(tmp_path / "파이프"))
    os.symlink(str(tmp_path / "메모.txt"), str(tmp_path / "링크.txt"))

    paths = ["/"] + [
        str(tmp_path / name)
        for name in ("폴더A", "메모.txt", "README", "파이프", "링크.txt")
    ]
    plain = QFileIconProvider()
    store = FavoritesStore(base_dir=str(tmp_path / "fav"))

    # 어느 것을 먼저 묻든 결과가 같아야 한다
    for order in (paths, list(reversed(paths))):
        provider = CategoryIconProvider(store)
        for path in order:                            # 캐시를 이 순서로 채운다
            provider.icon(QFileInfo(path))
        for path in paths:
            assert _icon_blob(provider.icon(QFileInfo(path))) == _icon_blob(
                plain.icon(QFileInfo(path))
            ), (path, order[0])

    # "같은 종류는 한 번만 묻는다"는 test_icon_provider_asks_qt_once_per_kind
    # 가, "Qt 가 가르는 것은 우리도 가른다"는 test_icon_key_splits_whatever_qt_
    # splits 가 본다. 여기서 캐시 크기까지 재려 했더니 우리 _suffix 로 기대값을
    # 만드는 자기참조가 되어 아무것도 못 잡았다 — 그 단언은 뺐다.


def test_icon_key_splits_whatever_qt_splits(qapp, tmp_path):
    """Qt 가 **다른 종류로 보는 것**은 캐시 열쇠도 갈라야 한다.

    아이콘 그림으로 비교하면 안 된다 — 아이콘 테마에 그 종류의 그림이 없으면
    Qt 가 전부 같은 폴백을 주어 **결함이 있어도 통과**한다(실제로 이 환경이
    그렇다). 그래서 Qt 가 종류를 정하는 근거(``QMimeDatabase``)와 우리 열쇠를
    직접 맞춰 본다.

    실제로 났던 일: 확장자를 **파일일 때만** 봤더니 끊긴 링크가 확장자와 무관
    하게 한 칸에 묶여, 먼저 물어본 쪽 아이콘을 서로 덮어썼다. 분류 폴더는 안이
    전부 링크이고 대상이 지워지면 끊긴 링크가 되므로 이 화면에서 바로 드러난다.

    열쇠는 **제품 코드의 :meth:`_icon_key` 를 그대로 불러서** 본다. 예전에는
    이 테스트가 열쇠를 손으로 다시 조립했는데, 그러면 조립 규칙이 바뀌어도
    테스트는 옛 규칙을 계속 검사한다 — 실제로 그 사이에 들어간 조건 하나가
    통째로 검사 밖이었다(결함을 심어도 278개가 전부 통과).
    """
    from qtpy.QtCore import QFileInfo, QMimeDatabase, QStandardPaths

    from custom_file_dialog.icons import CategoryIconProvider
    from custom_file_dialog.qt_compat import scoped_attr

    (tmp_path / "폴더").mkdir()
    os.mkfifo(str(tmp_path / "파이프"))
    for name in ("글.txt", "그림.png", "묶음.tar.gz", "그냥.gz", "소스.c", "소스.C"):
        (tmp_path / name).write_text("x")
    for name in (".숨김.txt", ".숨김.png"):               # 점파일도 확장자를 본다
        (tmp_path / name).write_text("x")
    for name in ("끊긴.txt", "끊긴.png"):                 # 대상이 없는 링크
        os.symlink(str(tmp_path / "없음"), str(tmp_path / name))

    names = [
        "파이프", "글.txt", "그림.png", "묶음.tar.gz", "그냥.gz",
        "소스.c", "소스.C", "끊긴.txt", "끊긴.png", "없는것.txt", "없는것.png",
        ".숨김.txt", ".숨김.png",
    ]
    database = QMimeDatabase()
    provider = CategoryIconProvider(FavoritesStore(base_dir=str(tmp_path / "fav")))

    seen = {}
    for name in names:
        info = QFileInfo(str(tmp_path / name))
        key = provider._icon_key(info)
        assert key is not None, name
        seen.setdefault(key, set()).add(database.mimeTypeForFile(info).name())

    mixed = {key: kinds for key, kinds in seen.items() if len(kinds) > 1}
    assert not mixed, "한 열쇠에 Qt 종류가 여럿 묶였다: %s" % mixed

    # **평범한 폴더는 한 칸에 모이고, 특수 폴더는 따로 간다.** Qt6 은 홈과
    # 바탕화면에 XDG 전용 아이콘을 주므로(실측: Qt6 + gtk3 에서만, 그 둘뿐)
    # 통째로 묶으면 평범한 폴더가 그 모양으로 오염된다. 그렇다고 이름을 열쇠에
    # 넣으면 폴더 수만큼 열쇠가 나 캐시가 죽는다 — 네트워크 홈은 폴더가
    # 대부분이라 하필 가장 비싼 자리에서 그렇게 된다.
    (tmp_path / "폴더2").mkdir()
    plain_dirs = {provider._icon_key(QFileInfo(str(tmp_path / n)))
                  for n in ("폴더", "폴더2")}
    assert len(plain_dirs) == 1, plain_dirs
    home = QStandardPaths.writableLocation(
        scoped_attr(QStandardPaths, "StandardLocation", "HomeLocation"))
    assert provider._icon_key(QFileInfo(home)) not in plain_dirs

    # 그러면서도 같은 종류는 한 칸에 모인다 — 열쇠가 이름마다 갈리면 캐시가 죽는다.
    # 날짜·버전이 박힌 이름이 그 자리다(``로그.2024.01.txt``). 확장자 사슬을
    # 열쇠로 쓰던 때는 여기서 파일 수만큼 열쇠가 났다(실측 2,000개 -> 2,000개).
    keys = set()
    for day in range(30):
        name = "로그.2024.%02d.txt" % (day + 1)
        (tmp_path / name).write_text("x")
        keys.add(provider._icon_key(QFileInfo(str(tmp_path / name))))
    assert len(keys) == 1, keys


def test_mime_key_ignores_a_real_file_of_the_same_fake_name(qapp, tmp_path, monkeypatch):
    """종류를 정할 때 쓰는 **가짜 이름**이 현재 폴더의 진짜 파일에 걸리면 안 된다.

    확장자로 종류를 알아내려고 ``x.<확장자>`` 라는 없는 이름을 넘기는데, 기본
    방식(``MatchDefault``)은 그 이름을 **현재 작업 폴더 기준의 실제 경로**로 보고
    내용까지 읽는다. 마침 그 이름의 파일이 거기 있으면 남의 파일 내용이 우리
    아이콘 열쇠로 새어 든다 — 실측: 현재 폴더에 PNG 내용이 든 ``x`` 가 있으면
    확장자 없는 파일 전부가 image/png 로 분류됐다. 그래서 ``MatchExtension`` 으로
    이름만 보게 한다(그 편이 네트워크에서 읽기 왕복도 없다).
    """
    from custom_file_dialog import icons as icons_module

    monkeypatch.chdir(tmp_path)
    (tmp_path / "x").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    icons_module._mime_names.clear()
    assert icons_module._mime_name("") == "application/octet-stream"


def test_shared_recent_store_is_not_trimmed_by_a_smaller_widget(tmp_path):
    """개수가 다른 두 곳이 **같은 기본 위치**를 써도 큰 쪽 목록이 안 줄어든다.

    ``recent_files=True`` 로 자동 생성하면 모두 같은 폴더를 가리킨다. 작은 쪽이
    한 번 기록하는 순간 ``_evict`` 가 자기 기준으로 잘라 링크를 **디스크에서
    지웠다**(30개 -> 20개, 4개 바인딩 전부 재현). 같은 규칙이
    :meth:`PathHistory.add` 와 ``PlacesOptions.update`` 에는 이미 있었는데
    정작 자르는 자리에만 빠져 있었다.
    """
    shared = str(tmp_path / "recent")
    big = RecentStore(base_dir=shared, max_items=30)
    small = RecentStore(base_dir=shared, max_items=5)
    assert big.base_dir == small.base_dir

    paths = []
    for index in range(30):
        path = tmp_path / ("f%02d.csv" % index)
        path.write_text("x")
        paths.append(str(path))
        big.record(str(path))
    assert len(big.items()) == 30

    small.record(paths[10])                     # 이미 있는 것을 다시 골라도
    assert len(big.items()) == 30, "재기록만으로 줄었다"
    small.record(paths[0])
    assert len(big.items()) == 30

    newer = tmp_path / "새것.csv"                # 새 파일이면 맨 앞으로, 개수는 유지
    newer.write_text("x")
    small.record(str(newer))
    assert len(big.items()) == 30
    assert big.items()[0] == str(newer)
    assert os.path.exists(big.items()[-1])      # 밀려난 것도 원본은 남는다

    # 의도적으로 줄이는 것은 그대로 동작해야 한다
    big.set_max_items(3)
    assert len(big.items()) == 3


def test_drawn_icons_are_shared_across_places(qapp, tmp_path):
    """같은 아이콘을 **다이얼로그마다 새로 그리지 않는다.**

    별표·시계·집은 상태가 없어 프로세스에 하나면 된다. 캐시가 Places 인스턴스에
    묶여 있었더니 다이얼로그를 띄울 때마다 QIcon 5벌(픽스맵 25장)이 새로
    그려졌고, PyQt 는 회수하지만 **PySide 는 못 해서** 반복해 여는 앱에서 그대로
    쌓였다(실측: PySide2 회당 452KB · QIcon +9개 -> 고친 뒤 18.6KB · +0개).
    """
    from custom_file_dialog import Places
    from custom_file_dialog.icons import clock_icon, home_icon, star_icon

    assert star_icon() is star_icon()
    assert home_icon() is home_icon()
    assert clock_icon() is clock_icon()
    assert star_icon() is not clock_icon()
    # 인자가 다르면 다른 아이콘이다 — 캐시가 전부를 뭉뚱그리지 않는다
    assert star_icon(color="#111111") is not star_icon()
    assert star_icon(sizes=(16,)) is not star_icon()

    # **QColor 로 줘도** 색마다 제대로 갈려야 한다. 열쇠에 str(color) 를 쓰면
    # PyQt 에서는 값이 아니라 객체 주소가 나와, 팔레트에서 색을 뽑아 아이콘을
    # 여러 개 만들면 먼저 그린 색이 그대로 돌아왔다(PyQt5 5개 중 4개가 틀림).
    from qtpy.QtGui import QColor

    wanted = ["#e53935", "#43a047", "#1e88e5", "#fdd835"]
    # 별 **한가운데**를 본다 — 가장자리는 안티앨리어싱으로 섞인다
    drawn = [star_icon(color=QColor(name), sizes=(32,)) for name in wanted]
    got = [icon.pixmap(32, 32).toImage().pixelColor(16, 16).name() for icon in drawn]
    assert got == wanted, got
    # 같은 색을 문자열로 줘도 같은 아이콘이다
    assert star_icon(color=QColor("#e53935"), sizes=(32,)) is star_icon(
        color="#e53935", sizes=(32,)
    )

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    first, second = Places(favorites=store), Places(favorites=store)
    assert first.home_icon() is second.home_icon()
    assert first.category_icon(store) is second.category_icon(store)


def test_hardlink_fallback_when_symlinks_are_unavailable(tmp_path, monkeypatch):
    """심볼릭 링크를 못 만드는 곳에서는 **하드링크로** 등록된다.

    윈도우 비개발자 모드·FAT32·일부 CIFS 가 그렇다. 이 컨테이너는 심볼릭
    링크가 늘 되므로 이 갈래는 어떤 테스트도 지나지 않았다 — ``os.link`` 를
    통째로 없애도 전체 테스트가 통과했다.

    하드링크는 원본과 동등한 경로라 링크에서 원본을 되찾을 수 없다. 그래서
    인덱스 파일이 그 몫을 한다(:data:`INDEX_FILENAME` 의 존재 이유).
    """
    real_symlink = os.symlink

    def no_symlinks(*args, **kwargs):
        raise OSError(1, "심볼릭 링크를 만들 수 없습니다")

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    target = tmp_path / "설계도.csv"
    target.write_text("x")

    monkeypatch.setattr(os, "symlink", no_symlinks)
    link = store.add("설계", str(target))

    assert os.path.exists(link)
    assert not os.path.islink(link)                  # 심볼릭이 아니라 하드링크
    assert os.stat(link).st_ino == os.stat(str(target)).st_ino

    # 링크로는 원본을 못 되찾으므로 인덱스가 그 몫을 한다
    assert store.resolve(link) == str(target)
    assert store.items("설계") == [str(target)]
    assert store.contains("설계", str(target))

    # 원본 경로로 지울 수 있고, 원본 자체는 남는다
    assert store.remove("설계", str(target))
    assert store.items("설계") == []
    assert target.exists()

    monkeypatch.setattr(os, "symlink", real_symlink)


def test_folder_needs_symlink_or_junction(tmp_path, monkeypatch):
    """폴더는 하드링크가 안 되므로, 심볼릭 링크가 없으면 알려 주고 실패한다."""
    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    folder = tmp_path / "산출물"
    folder.mkdir()

    monkeypatch.setattr(
        os, "symlink", lambda *a, **k: (_ for _ in ()).throw(OSError(1, "안 됨"))
    )
    with pytest.raises(FavoritesError) as caught:
        store.add("설계", str(folder))
    assert "즐겨찾기 링크를 만들지 못했습니다" in str(caught.value)
    assert store.items("설계") == []


def test_store_creation_gives_up_on_dead_home(tmp_path, monkeypatch):
    """저장소를 **만드는 것** 자체가 멈춘 네트워크 홈에서 매달리지 않는다.

    저장소의 기본 자리는 ``~/.config`` — 이 라이브러리가 상정하는 네트워크 홈
    위다. 그런데 생성자가 맨 ``os.makedirs`` 로 만들고 있어서, 홈이 멈추면
    ``path_timeout`` 을 아무리 낮춰 줘도 **다이얼로그를 열기도 전에** 앱이
    잡혔다(실측 6.00초). 안전장치를 켜 두라고 안내하고는 정작 첫 걸음에서 그
    값을 무시한 셈이다.
    """
    from custom_file_dialog import safety

    dead = str(tmp_path / "홈")
    os.makedirs(dead)
    _fake_remote_mount(monkeypatch, dead)      # 그 자리를 nfs4 로 위장

    real = os.makedirs

    def hang(path, *args, **kwargs):
        if str(path).startswith(dead):
            time.sleep(2.0)                    # mkdir 이 돌아오지 않는다
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "makedirs", hang)
    try:
        for cls in (FavoritesStore, RecentStore):
            start = time.time()
            store = cls(base_dir=os.path.join(dead, ".config", "store"))
            spent = time.time() - start
            # 안전장치가 걸리면 예산(_fake_remote_mount 의 0.2초)만 쓴다.
            # 걸리지 않으면 makedirs 두 번 × 2.0초 = 4.0초다.
            assert spent < 1.5, "%s 생성이 %.2f초 매달렸다" % (cls.__name__, spent)
            # 물러섰을 뿐 쓸 수는 있는 물건이어야 한다 — 조회는 빈 값이다
            assert store.categories() == []
    finally:
        # **뒷정리를 끝까지 한다.** _fake_remote_mount 가 걸어 둔
        # timeout=0.2 를 그대로 두면 뒤따르는 모든 테스트가 짧은 예산으로
        # 돌고, 여기서 만든 "안 돌아오는" 스레드가 살아 있는 채로 다음
        # 테스트에 넘어간다 — 스위트를 들쭉날쭉하게 만드는 조합이다.
        safety.reset()
        deadline = time.time() + 10.0
        while safety_reach.pending_checks() and time.time() < deadline:
            time.sleep(0.05)
        safety.clear_cache()
