"""우클릭 메뉴(FavoritesMenus) — 추가 · 제거 · 사이드바 정리."""

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
    assert menus._places.favorites is None
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
    from custom_file_dialog.constants import PATH_ROLE as URL_ROLE

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



def test_copy_path_menu(qapp, tmp_path):
    """파일 목록 우클릭에 "경로 복사"가 구분선으로 나뉘어 붙고, 실제로 복사된다."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog, menus = _menu_dialog(store, os.path.dirname(design))
    dialog.show()
    _spin(qapp, 400)

    copied = []
    menus.pathCopied.connect(copied.append)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    rows = {model.index(r, 0, root).data(): model.index(r, 0, root)
            for r in range(model.rowCount(root))}

    _path, menu = _view_menu(menus, tree, rows["설계도.csv"])
    assert "경로 복사" in _menu_labels(menu)

    # 구분선으로 나뉘어 있다 — 바로 앞뒤 어느 한쪽에 separator 가 있다
    actions = menu.actions()
    at = [a.text() for a in actions].index("경로 복사")
    assert actions[at - 1].isSeparator() and actions[at + 1].isSeparator()

    action = [a for a in actions if a.text() == "경로 복사"][0]
    action.trigger()
    assert QApplication.clipboard().text() == design
    assert copied == [design]
    dialog.close()


def test_copy_path_resolves_links(qapp, tmp_path):
    """분류 안의 링크에서 "경로 복사"는 링크가 아니라 **원본 경로**를 복사한다."""
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    link = store.add("설계", design)

    dialog, menus = _menu_dialog(store, str(tmp_path))
    assert menus.copy_path(link) == design               # 링크 -> 원본
    assert QApplication.clipboard().text() == design

    assert menus.copy_path(design) == design             # 일반 경로는 그대로
    assert menus.copy_path("") is None                   # 빈 경로는 아무 일 없음
    dialog.close()


def test_view_menu_never_stats_automount_paths(qapp, tmp_path, monkeypatch):
    """우클릭 메뉴 구성이 automount 위 경로를 stat 하지 않는다.

    메뉴는 GUI 스레드에서 그 자리에서 만들어지므로, 쓰기 가능 여부 확인이
    원격/automount 경로를 그대로 만지면 메뉴가 뜨는 순간 멈춘다. 만질 수 없는
    자리는 확인 없이 이름 변경·삭제만 비활성으로 둔다.
    """
    from qtpy.QtWidgets import QMenu

    from custom_file_dialog import safety


    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    dialog, menus = _menu_dialog(store, str(tmp_path))

    root = tmp_path / "user"
    root.mkdir()
    safety.clear_cache()
    monkeypatch.setattr(
        safety_mounts,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (str(root), "autofs", "auto.user"),
        ],
    )

    touched = []
    real_access = os.access

    def counting_access(path, *args, **kwargs):
        touched.append(str(path))
        return real_access(path, *args, **kwargs)

    monkeypatch.setattr(os, "access", counting_access)
    try:
        menu = QMenu()
        menus._add_default_actions(menu, str(root / "myaccount" / "f.csv"))
        assert not [p for p in touched if p.startswith(str(root))]

        # 로컬 경로는 평소대로 확인한다. 보는 것은 **담고 있는 폴더**다 —
        # 이름 변경·삭제 권한은 파일이 아니라 그 폴더의 것이기 때문이다.
        local = _touch(tmp_path, "일반.txt")
        menus._add_default_actions(QMenu(), local)
        assert os.path.dirname(local) in touched
    finally:
        safety.clear_cache()
    dialog.close()


def test_view_menu_hides_add_inside_stores(qapp, tmp_path):
    """저장소 안(분류 폴더 · 최근 파일)에서는 "즐겨찾기에 추가" 를 띄우지 않는다."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    recent.record(design)

    # 저장소 뿌리에서 분류 폴더를 우클릭한 상황
    dialog, menus = _menu_dialog([store, recent], store.base_dir)
    dialog.show()
    _spin(qapp, 400)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    rows = {model.index(r, 0, root).data(): model.index(r, 0, root)
            for r in range(model.rowCount(root))}
    assert "설계" in rows, sorted(rows)

    _path, menu = _view_menu(menus, tree, rows["설계"])
    assert _submenu_of(menu) is None                  # 추가 메뉴가 없다
    # 코드에서 직접 불러도 막힌다
    assert not menus.add_to_favorites(store.category_dir("설계"), "보고서")
    assert store.categories() == ["설계"]
    dialog.close()


def test_add_to_favorites_survives_os_error(qapp, tmp_path, monkeypatch):
    """저장소 폴더를 만들지 못해도(읽기 전용 홈 등) 예외가 새지 않는다.

    우클릭 슬롯에서 예외가 튀면 PyQt5.5+ 는 앱을 abort 시킨다.
    """
    from qtpy.QtWidgets import QMessageBox

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    dialog, menus = _menu_dialog(store, str(tmp_path))

    def deny(*args, **kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "makedirs", deny)
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.append(a[-1]) or 0
    )

    assert menus.add_to_favorites(design, "설계") is False   # 조용히 실패
    assert warned                                            # 사용자에게 알린다
    dialog.close()


def test_remove_category_uses_the_clicked_store(qapp, tmp_path):
    """같은 이름의 분류가 둘이어도 우클릭한 그 저장소에서 지운다.

    이름으로 다시 찾으면 최근 파일이 먼저 잡혀, 즐겨찾기 분류를 지우려다
    **최근 파일 목록이 통째로 날아갔다.**
    """
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"))
    design, _report, _output = _make_tree(tmp_path)
    recent.record(design)
    store.add(recent.name, design)          # 즐겨찾기에 "최근 파일" 이라는 분류

    dialog, menus = _menu_dialog([store, recent], str(tmp_path), confirm=False)
    assert menus.remove_category(recent.name, store=store)

    assert recent.name not in store.categories()     # 즐겨찾기 쪽만 사라지고
    assert recent.items() == [design]                # 최근 목록은 그대로다
    dialog.close()


def test_new_category_gets_its_icon_right_away(qapp, tmp_path):
    """실행 중에 만든 분류도 곧바로 별표로 보인다(폴더 아이콘으로 남지 않는다)."""
    from qtpy.QtWidgets import QListView, QStyleOptionViewItem

    from custom_file_dialog import CustomFileDialog

    design, _report, _output = _make_tree(tmp_path)
    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    store.add("설계", design)

    dialog = CustomFileDialog(
        None, mode="open_file", directory=os.path.dirname(design), favorites=store
    )
    dialog.show()
    _spin(qapp, 300)

    menus = [c for c in dialog.children() if type(c).__name__ == "FavoritesMenus"][0]
    assert menus.add_to_favorites(design, "새분류")
    _spin(qapp, 200)

    sidebar = dialog.findChild(QListView, "sidebar")
    delegate = sidebar.itemDelegate()
    model = sidebar.model()
    drawn = {}
    for row in range(model.rowCount()):
        option = QStyleOptionViewItem()
        delegate.initStyleOption(option, model.index(row, 0))
        drawn[option.text] = option.icon.availableSizes() if option.icon else []

    star = dialog.places().category_icon(store).availableSizes()
    assert drawn.get("새분류") == star, drawn
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_sidebar_menu_installed_without_stores(qapp, tmp_path):
    """저장소가 없어도 보호 위치를 지정했으면 사이드바 메뉴가 걸린다.

    그 메뉴가 "사이드바에서 제거"를 막는 곳이다. 걸지 않으면 Qt 기본 Remove 가
    남아 지정한 자리가 그냥 빠졌다(창만 비네이티브로 바뀌고 지정은 무시).
    """
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QListView

    from custom_file_dialog import CustomFileDialog

    home = os.path.expanduser("~")
    dialog = CustomFileDialog(
        None, mode="open_file", directory=str(tmp_path), fixed_sidebar_urls=[home]
    )
    dialog.show()
    _spin(qapp, 200)

    menus = [c for c in dialog.children() if type(c).__name__ == "FavoritesMenus"]
    assert menus, "보호 위치만 지정해도 메뉴가 걸려야 한다"
    assert menus[0].is_fixed(home)

    sidebar = dialog.findChild(QListView, "sidebar")
    assert sidebar.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    assert not menus[0].remove_sidebar_entry(home)      # 보호가 실제로 걸린다
    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def test_menus_reject_missing_places(qapp):
    """저장소 묶음을 아예 안 주면 ``AttributeError`` 가 아니라 False 다."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import FavoritesMenus

    dialog = QFileDialog()
    dialog.setOptions(QFileDialog.Option.DontUseNativeDialog)
    assert FavoritesMenus(dialog, None).install() is False
    dialog.deleteLater()
    _spin(qapp, 50)


def test_read_only_dialog_cannot_make_folders(qapp, tmp_path):
    """ReadOnly 로 연 다이얼로그에서 우클릭으로 폴더가 만들어지지 않는다.

    install() 이 Qt 기본 컨텍스트 메뉴를 통째로 떼어 내므로, Qt 가 메뉴를 띄울
    때마다 하던 "새 폴더" 항목 활성 갱신도 함께 사라졌다.
    """
    from qtpy.QtWidgets import QFileDialog, QMenu, QToolButton

    from custom_file_dialog import FavoritesMenus, Places

    try:
        from qtpy.QtGui import QAction
    except ImportError:
        from qtpy.QtWidgets import QAction

    dialog = QFileDialog()
    dialog.setOptions(
        QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ReadOnly
    )
    dialog.setDirectory(str(tmp_path))
    dialog.show()
    _spin(qapp, 100)

    menus = FavoritesMenus(dialog, Places())
    assert menus.install()

    menu = QMenu(dialog)
    menus._add_default_actions(menu, None)
    new_folder = dialog.findChild(QAction, "qt_new_folder_action")
    button = dialog.findChild(QToolButton, "newFolderButton")
    assert new_folder is not None and button is not None
    assert not button.isEnabled()            # Qt 자신의 기준
    assert not new_folder.isEnabled()        # 우리 메뉴도 같아야 한다
    # (비활성이면 메뉴에서 누를 수 없다. QAction.trigger() 는 활성 여부를 보지
    #  않으므로 실제 클릭을 흉내 내지 못해 여기서 확인하지 않는다.)

    dialog.done(0)
    dialog.deleteLater()
    _spin(qapp, 50)


def _recent_dialog(qapp, tmp_path):
    """최근 파일 폴더를 연 다이얼로그와 그 안의 링크 하나를 돌려준다."""
    from qtpy.QtWidgets import QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)          # 즐겨찾기에도 분류가 하나 있어야 메뉴가 채워진다
    recent.record(design)

    dialog, menus = _menu_dialog([store, recent], recent.category_dir("최근 파일"))
    dialog.show()
    _spin(qapp, 400)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    assert model.rowCount(root) == 1, "최근 파일에 항목이 하나 있어야 한다"
    return dialog, menus, tree, model.index(0, 0, root), store, design


def test_add_to_favorites_from_recent(qapp, tmp_path):
    """최근 파일의 항목에도 "즐겨찾기에 추가" 가 붙고, **원본**이 등록된다."""
    dialog, menus, tree, index, store, design = _recent_dialog(qapp, tmp_path)

    _path, menu = _view_menu(menus, tree, index)
    submenu = _submenu_of(menu)
    assert submenu is not None, "최근 파일에서도 추가 메뉴가 떠야 한다"
    assert submenu.title() == "즐겨찾기에 추가"

    labels = [a.text() for a in submenu.actions() if not a.isSeparator()]
    assert "설계" in labels and "새 분류..." in labels, labels
    # 이미 '설계' 에 들어 있는 원본이라 그 항목은 꺼져 있어야 한다 —
    # 링크 경로로 판정했다면 원본을 못 찾아 켜져 있었을 것이다.
    assert not [a for a in submenu.actions() if a.text() == "설계"][0].isEnabled()

    # 다른 분류로 실제로 넣어 보면 링크가 아니라 **원본**을 가리켜야 한다.
    assert menus.add_to_favorites(design, "보고서")
    assert store.resolve(store.link_for("보고서", design)) == design
    dialog.close()


def test_recent_menu_omits_rename_and_hidden(qapp, tmp_path):
    """최근 파일에서는 "이름 변경" · "숨김 파일 표시" 를 아예 안 붙인다."""
    from qtpy.QtWidgets import QAction

    dialog, menus, tree, index, _store, _design = _recent_dialog(qapp, tmp_path)
    _path, menu = _view_menu(menus, tree, index)

    dropped = [dialog.findChild(QAction, name)
               for name in ("qt_rename_action", "qt_show_hidden_action")]
    assert all(a is not None for a in dropped), "Qt 기본 항목을 못 찾았다"
    for action in dropped:
        assert action not in menu.actions(), action.objectName()

    # 뜻이 있는 것은 그대로 남는다
    assert [a for a in menu.actions() if a.text().endswith("에서 제거")]
    assert "경로 복사" in _menu_labels(menu)
    # 구분선만 둘 연달아 남지 않는다
    kinds = [a.isSeparator() for a in menu.actions()]
    assert not any(kinds[i] and kinds[i + 1] for i in range(len(kinds) - 1)), kinds
    dialog.close()


def test_favorites_category_still_hides_add(qapp, tmp_path):
    """즐겨찾기 분류 안은 그대로 — 최근 파일만 열어 준 것이다."""
    from qtpy.QtWidgets import QAction, QTreeView

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog, menus = _menu_dialog([store, recent], store.category_dir("설계"))
    dialog.show()
    _spin(qapp, 400)

    tree = dialog.findChild(QTreeView, "treeView")
    model, root = tree.model(), tree.rootIndex()
    _path, menu = _view_menu(menus, tree, model.index(0, 0, root))
    assert _submenu_of(menu) is None
    # 이름 변경도 여기서는 계속 붙는다(최근 파일이 아니므로)
    rename = dialog.findChild(QAction, "qt_rename_action")
    assert rename in menu.actions()
    dialog.close()
