"""사이드바 — 항목 목록 · 순서 · 홈/현재 위치 표시 · 폭 · 아이콘."""

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
    assert seen["options"] & qt_compat.option_value("DontUseNativeDialog")
    assert seen["file_mode"] == qt_compat.enum_value("FileMode", "ExistingFiles")


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


def test_sidebar_width_fits_items(qapp, tmp_path):
    """사이드바가 처음 열릴 때 항목이 잘리지 않을 만큼 넓어진다."""
    from qtpy.QtWidgets import QListView, QSplitter

    from custom_file_dialog import CustomFileDialog

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=5)
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("아주긴분류이름입니다", design)
    recent.record(design)

    def widths(**kwargs):
        dialog = CustomFileDialog(
            None, mode="open_file", directory=os.path.dirname(design),
            favorites=favorites, recent=recent, **kwargs
        )
        dialog.resize(900, 600)
        dialog.show()
        _spin(qapp, 300)
        sidebar = dialog.findChild(QListView, "sidebar")
        splitter = dialog.findChild(QSplitter, "splitter")
        result = (sidebar.width(), splitter.sizes()[1], sidebar.sizeHintForColumn(0))
        dialog.close()
        return result

    # 기본: 내용이 필요한 만큼은 확보된다
    width, _files, needed = widths()
    assert width >= needed
    assert width >= sidebar_module.MIN_SIDEBAR_WIDTH

    # 직접 지정하면 그대로
    assert widths(sidebar_width=220)[0] == 220

    # 0 이면 내용에 맞추지는 않지만, 최소 폭 아래로는 내려가지 않는다
    untouched = widths(sidebar_width=0)[0]
    assert untouched >= sidebar_module.MIN_SIDEBAR_WIDTH

    # 아무리 넓게 줘도 파일 목록 자리는 남긴다
    assert widths(sidebar_width=5000)[1] >= 200


def test_sidebar_width_respects_user_drag(qapp, tmp_path):
    """사용자가 경계를 끌어 좁힌 뒤 다시 열어도 되돌리지 않는다."""
    from qtpy.QtWidgets import QListView, QSplitter

    from custom_file_dialog import CustomFileDialog

    favorites = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    favorites.add("설계", design)

    dialog = CustomFileDialog(
        None, mode="open_file", directory=os.path.dirname(design), favorites=favorites
    )
    dialog.resize(900, 600)
    dialog.show()
    _spin(qapp, 300)

    splitter = dialog.findChild(QSplitter, "splitter")
    total = sum(splitter.sizes())
    splitter.setSizes([60, total - 60])          # 사용자가 끌어서 좁혔다
    _spin(qapp, 100)
    narrowed = dialog.findChild(QListView, "sidebar").width()

    dialog.hide()
    dialog.show()                                 # 다시 열어도
    _spin(qapp, 300)
    assert dialog.findChild(QListView, "sidebar").width() == narrowed
    dialog.close()


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

