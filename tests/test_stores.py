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


def test_icon_provider_only_inside_stores(qapp, tmp_path):
    """분류 아이콘 제공자는 저장소 폴더를 볼 때만 걸려 있다.

    제공자는 목록의 **항목마다** 파이썬으로 불려서, 항목이 수천 개인 폴더(홈)
    에서는 그것만으로 GUI 가 100ms 단위로 멈춘다(실측 190ms -> 103ms). 분류
    아이콘이 필요한 자리는 저장소 안뿐이다.
    """
    from qtpy.QtWidgets import QListView

    from custom_file_dialog import CustomFileDialog
    from custom_file_dialog.icons import CategoryIconProvider

    design, _report, _output = _make_tree(tmp_path)
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store.add("설계", design)

    dialog = CustomFileDialog(
        None, mode="open_file", directory=os.path.dirname(design), favorites=store
    )
    dialog.show()
    _spin(qapp, 300)

    # 평범한 폴더 -> 기본 제공자
    assert not isinstance(dialog.iconProvider(), CategoryIconProvider)

    # 저장소 안으로 들어가면 분류 아이콘 제공자가 걸린다
    dialog.setDirectory(store.base_dir)
    dialog.directoryEntered.emit(store.base_dir)      # 사용자 이동과 같은 신호
    assert isinstance(dialog.iconProvider(), CategoryIconProvider)

    dialog.setDirectory(str(tmp_path))
    dialog.directoryEntered.emit(str(tmp_path))
    assert not isinstance(dialog.iconProvider(), CategoryIconProvider)

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


def test_from_options_keeps_old_signature(tmp_path):
    """예전 이름은 위치 인자로 부르던 코드도 계속 받아 준다."""
    from custom_file_dialog import Places

    store = FavoritesStore(base_dir=str(tmp_path / "fav"))
    places = Places.from_options(store)              # 위치 인자
    assert places.favorites_store() is store
    assert Places.from_options(favorites=store).favorites_store() is store


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


def test_existing_recent_dir_is_kept_after_rule_change(tmp_path):
    """규칙이 바뀌어도 **이미 쌓인** 최근 목록은 계속 쓴다.

    configure_favorites 만 쓰던 앱은 그동안 "즐겨찾기 폴더의 부모/recent" 를
    써 왔다. 새 규칙(저장소 뿌리 아래)만 보면 그 목록이 하루아침에 빈 것처럼
    보이고, 옛 폴더는 고아가 된다.
    """
    from custom_file_dialog import configure_favorites, configure_storage
    from custom_file_dialog.recent import default_recent_dir, legacy_recent_dir

    favorites_dir = tmp_path / "앱데이터" / "favorites"
    favorites_dir.mkdir(parents=True)
    try:
        configure_storage(str(tmp_path / "뿌리"))
        configure_favorites(str(favorites_dir))

        # 옛 자리에 목록이 없으면 새 규칙대로
        assert default_recent_dir() == os.path.join(
            os.path.normpath(str(tmp_path / "뿌리")), "recent"
        )

        # 이름만 같은 **남의 폴더**는 뺏지 않는다(우리 표식이 없다)
        old_dir = legacy_recent_dir()
        os.makedirs(old_dir)
        assert default_recent_dir() != old_dir

        # 우리가 쌓아 둔 저장소면 그것을 계속 쓴다
        os.makedirs(os.path.join(old_dir, "최근 파일"))
        assert default_recent_dir() == old_dir
        assert RecentStore().base_dir == os.path.normpath(old_dir)
    finally:
        configure_favorites(None)
        configure_storage(None)


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
