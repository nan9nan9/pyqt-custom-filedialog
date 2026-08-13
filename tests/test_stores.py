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
        safety, "iter_mounts",
        lambda refresh=False: [("/", "ext4", "/dev/sda1"),
                               (mountpoint, "nfs4", "srv:/export")],
    )
    monkeypatch.setattr(safety, "probe_host", lambda *a, **k: True)
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
