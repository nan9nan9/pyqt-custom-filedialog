"""exec_file_dialog 래퍼와 CustomFileDialog — 반환 정규화 · 용도별 시작 위치."""

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


def test_exec_file_dialog_dispatch(qapp, monkeypatch):
    """모드별로 알맞은 QFileDialog 정적 메서드가 호출되고 결과가 정규화된다."""
    from qtpy.QtWidgets import QFileDialog

    seen = {}

    def record(name, ret):
        def fake(*args):
            seen[name] = args
            return ret

        return staticmethod(fake)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", record("open", ("/a.txt", "텍스트 (*.txt)"))
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", record("opens", (["/a.txt", "/b.txt"], ""))
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", record("save", ("/out", "CSV (*.csv)"))
    )
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", record("dir", "/some/dir"))

    paths, chosen = dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE)
    assert paths == ["/a.txt"] and chosen == "텍스트 (*.txt)"

    paths, _ = dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILES)
    assert paths == ["/a.txt", "/b.txt"]

    # 저장 모드는 선택된 필터에서 확장자를 유추해 붙여 준다
    paths, _ = dialog_module.exec_file_dialog(mode=SelectMode.SAVE_FILE)
    assert paths == ["/out.csv"]

    # 디렉터리 모드는 문자열 하나만 돌려주므로 리스트로 감싼다
    paths, _ = dialog_module.exec_file_dialog(mode=SelectMode.DIRECTORY)
    assert paths == ["/some/dir"]

    # native=False 면 DontUseNativeDialog 옵션이 켜져서 전달된다
    dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE, native=False)
    options = seen["open"][-1]
    assert options & qt_compat.option_value("DontUseNativeDialog")

    dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE, native=True)
    assert not (seen["open"][-1] & qt_compat.option_value("DontUseNativeDialog"))


def test_options_are_accepted_by_qt(qapp):
    """조립한 options 값을 실제 QFileDialog 가 받아들이는지 확인한다."""
    from qtpy.QtWidgets import QFileDialog

    dlg = QFileDialog()
    dlg.setOptions(dialog_module.make_options(native=False, show_dirs_only=True))
    assert dlg.options() & qt_compat.option_value("ShowDirsOnly")
    assert dlg.options() & qt_compat.option_value("DontUseNativeDialog")


def test_cancel_returns_empty(qapp, monkeypatch):
    from qtpy.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a: ("", ""))
    )
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a: "")
    )
    assert dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE)[0] == []
    assert dialog_module.exec_file_dialog(mode=SelectMode.DIRECTORY)[0] == []


def test_custom_file_dialog_is_a_qfiledialog(qapp, tmp_path):
    """QFileDialog 를 물려받아 원래 쓰던 API 가 그대로 통한다."""
    from qtpy.QtWidgets import QFileDialog

    from custom_file_dialog import CustomFileDialog

    assert issubclass(CustomFileDialog, QFileDialog)

    target = tmp_path / "data.csv"
    target.write_text("x")

    dialog = CustomFileDialog(
        None,
        mode="open_file",
        caption="입력 파일 선택",
        directory=str(tmp_path),
        filters=[("CSV", ["csv"])],
    )
    assert dialog.windowTitle() == "입력 파일 선택"
    assert dialog.nameFilters() == ["CSV (*.csv)"]
    assert dialog.directory().absolutePath() == str(tmp_path)
    assert dialog.mode() == SelectMode.OPEN_FILE
    assert dialog.fileMode() == QFileDialog.FileMode.ExistingFile
    assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptOpen
    # 커스터마이즈하려면 Qt 자체 다이얼로그여야 한다
    assert dialog.options() & QFileDialog.Option.DontUseNativeDialog

    # 제목을 안 주면 모드별 기본 제목
    assert CustomFileDialog(None, mode="directory").windowTitle() == "폴더 선택"
    dialog.deleteLater()


def test_custom_file_dialog_exec_and_result(qapp, tmp_path):
    """exec() 로 띄우고 selectedFiles() 로 받는다 — 취소하면 빈 결과."""
    from custom_file_dialog import CustomFileDialog

    target = tmp_path / "data.csv"
    target.write_text("x")

    dialog = CustomFileDialog(None, mode="open_file", directory=str(tmp_path))
    dialog.selectFile(str(target))
    _close_soon(dialog)
    assert _run(dialog)                              # if dlg.exec(): 가 통한다
    assert dialog.selectedFiles() == [str(target)]
    assert dialog.selectedPath() == str(target)

    cancelled = CustomFileDialog(None, mode="open_file", directory=str(tmp_path))
    _close_soon(cancelled, accepted=False)
    assert not _run(cancelled)
    assert cancelled.selectedPath() is None


def test_custom_file_dialog_modes(qapp, tmp_path):
    """모드마다 개수와 확장자 규칙이 exec_file_dialog 과 같다."""
    from custom_file_dialog import CustomFileDialog

    inner = tmp_path / "안쪽"
    inner.mkdir()
    for name in ("a.csv", "b.csv"):
        (inner / name).write_text("x")

    # 저장 모드: 확장자를 빼고 쳐도 붙는다
    save = CustomFileDialog(
        None, mode="save_file", directory=str(inner),
        filters=[("CSV", ["csv"])], default_suffix="csv",
    )
    save.selectFile("새파일")
    _close_soon(save)
    _run(save)
    assert save.selectedPath() == str(inner / "새파일.csv")

    # 폴더 모드
    folder = CustomFileDialog(None, mode="directory", directory=str(tmp_path))
    folder.selectFile(str(inner))
    _close_soon(folder)
    _run(folder)
    assert folder.selectedPath() == str(inner)

    # 한 개 모드는 여러 개가 골라져도 1개로 자른다
    single = CustomFileDialog(None, mode="open_file", directory=str(inner))
    single.selectFile(str(inner / "a.csv"))
    _close_soon(single)
    _run(single)
    assert len(single.selectedFiles()) == 1


def test_custom_file_dialog_stores_can_be_true(qapp, tmp_path, monkeypatch):
    """favorites=True / recent=True 면 기본 위치에 저장소를 만들어 준다."""
    from custom_file_dialog import (
        CustomFileDialog,
        FavoritesStore,
        RecentStore,
        configure_favorites,
    )

    monkeypatch.setattr(
        recent_module, "default_recent_dir", lambda: str(tmp_path / "recent")
    )
    configure_favorites(str(tmp_path / "favorites"))
    try:
        dialog = CustomFileDialog(None, mode="open_file", favorites=True, recent=True)
        places = dialog.places()
        assert isinstance(places.favorites_store(), FavoritesStore)
        assert isinstance(places.recent, RecentStore)

        # 개수도 지정할 수 있다
        assert CustomFileDialog(
            None, mode="open_file", recent=True, recent_max=5
        ).places().recent.max_items == 5

        # 인스턴스를 직접 주면 그대로 쓴다
        store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
        assert (
            CustomFileDialog(None, mode="open_file", favorites=store)
            .places()
            .favorites_store()
            is store
        )

        # 안 주면 없다
        empty = CustomFileDialog(None, mode="open_file").places()
        assert empty.favorites_store() is None
        assert empty.recent is None
    finally:
        configure_favorites(None)


def test_custom_file_dialog_store_contents_persist(qapp, tmp_path, monkeypatch):
    """띄울 때마다 저장소를 새로 만들어도 등록해 둔 것은 그대로다."""
    from qtpy.QtWidgets import QListView

    from custom_file_dialog import CustomFileDialog, configure_favorites

    design, _report, _output = _make_tree(tmp_path)
    configure_favorites(str(tmp_path / "favorites"))
    try:
        from custom_file_dialog import FavoritesStore

        FavoritesStore().add("설계", design)

        # 세 번 새로 띄워도 분류가 계속 보인다
        for _ in range(3):
            dialog = CustomFileDialog(None, mode="open_file", favorites=True)
            model = dialog.findChild(QListView, "sidebar").model()
            names = [model.index(r, 0).data() for r in range(model.rowCount())]
            assert "설계" in names
    finally:
        configure_favorites(None)


def test_safety_config_applies_to_dialog_made_after(qapp, guarded_root):
    """전역 safety 설정은 그 뒤에 만드는 다이얼로그에 자동으로 걸린다."""
    from qtpy.QtWidgets import QLineEdit

    from custom_file_dialog import CustomFileDialog, GuardedFileSystemModel, safety

    assert safety.is_guarded(guarded_root)

    dialog = CustomFileDialog(None, mode="open_file")
    name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
    model = name_edit.completer().model()
    assert isinstance(model, GuardedFileSystemModel)
    assert not model.canFetchMore(model.index(guarded_root))


def test_safety_config_after_dialog_is_too_late(qapp, tmp_path):
    """다이얼로그를 만든 뒤에 부르면 그 다이얼로그에는 안 걸린다(문서화된 함정)."""
    from qtpy.QtWidgets import QLineEdit

    from custom_file_dialog import CustomFileDialog, GuardedFileSystemModel, safety

    root = tmp_path / "user"
    root.mkdir()
    (root / "myaccount").mkdir()

    safety.reset()
    try:
        dialog = CustomFileDialog(None, mode="open_file")
        safety.configure(guarded_roots=[str(root)])

        # 판정 자체는 전역이라 최신이지만,
        assert safety.is_guarded(str(root))
        # 이미 만든 다이얼로그의 자동완성 모델은 갈아 끼워지지 않았다
        name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
        assert not isinstance(name_edit.completer().model(), GuardedFileSystemModel)

        # 그 뒤에 새로 만드는 것은 정상적으로 보호된다
        later = CustomFileDialog(None, mode="open_file")
        assert isinstance(
            later.findChild(QLineEdit, "fileNameEdit").completer().model(),
            GuardedFileSystemModel,
        )
    finally:
        safety.reset()


def test_custom_file_dialog_resolves_links(qapp, tmp_path):
    """즐겨찾기 링크를 골라도 selectedFiles() 는 원본 경로를 돌려준다."""
    from custom_file_dialog import CustomFileDialog

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)
    link = os.path.join(store.category_dir("설계"), "설계도.csv")

    dialog = CustomFileDialog(
        None, mode="open_file", directory=store.category_dir("설계"), favorites=store
    )
    dialog.selectFile(link)
    _close_soon(dialog)
    _run(dialog)

    assert dialog.selectedFiles() == [design]        # 링크가 아니라 원본
    assert dialog.places().favorites_store() is store


def test_custom_file_dialog_sidebar_and_settings_key(qapp, tmp_path, monkeypatch):
    """사이드바가 구성되고, settings_key 로 시작 위치를 주고받는다."""
    from qtpy.QtWidgets import QListView

    from custom_file_dialog import CustomFileDialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    dialog = CustomFileDialog(
        None, mode="open_file", directory=os.path.dirname(design),
        favorites=store, settings_key="입력csv",
    )
    sidebar = dialog.findChild(QListView, "sidebar")
    model = sidebar.model()
    names = [model.index(r, 0).data() for r in range(model.rowCount())]
    assert "설계" in names                       # 분류가 사이드바에 올라왔다

    dialog.selectFile(design)
    _close_soon(dialog)
    _run(dialog)
    assert last_dir("입력csv") == os.path.dirname(design)

    # 다음에 열면 거기서 시작한다 (directory 를 주지 않았을 때)
    again = CustomFileDialog(None, mode="open_file", settings_key="입력csv")
    assert again.directory().absolutePath() == os.path.dirname(design)

    # directory 를 주면 그쪽이 이긴다
    forced = CustomFileDialog(
        None, mode="open_file", directory=str(tmp_path), settings_key="입력csv"
    )
    assert forced.directory().absolutePath() == str(tmp_path)


def test_exec_file_dialog_uses_the_class(qapp, tmp_path, monkeypatch):
    """exec_file_dialog(places=...) 은 같은 클래스를 쓴다(구현이 하나다)."""
    from custom_file_dialog import CustomFileDialog

    built = []
    real_init = CustomFileDialog.__init__

    def spy_init(self, *args, **kwargs):
        built.append(kwargs.get("mode"))
        real_init(self, *args, **kwargs)
        _close_soon(self)

    monkeypatch.setattr(CustomFileDialog, "__init__", spy_init)

    target = tmp_path / "a.csv"
    target.write_text("x")
    monkeypatch.setattr(
        dialog_module.QFileDialog, "selectedFiles", lambda self: [str(target)]
    )

    paths, _chosen = dialog_module.exec_file_dialog(
        mode=SelectMode.OPEN_FILE,
        directory=str(tmp_path),
        places=Places(sidebar_urls=[str(tmp_path)]),
    )
    assert built == [SelectMode.OPEN_FILE]
    assert paths == [str(target)]


def test_exec_file_dialog_accepts_pythonic_filters(qapp, monkeypatch):
    """다이얼로그도 위젯과 같은 형태의 filters 를 받는다."""
    seen = []

    def fake_run(parent, mode, caption, directory, name_filter, *args):
        seen.append(name_filter)
        return [], ""

    monkeypatch.setattr(dialog_module, "_run_dialog", fake_run)

    def run(**kwargs):
        dialog_module.exec_file_dialog(mode=SelectMode.OPEN_FILE, **kwargs)
        return seen[-1]

    assert run(filters=[("이미지", ["png", "jpg"])]) == "이미지 (*.png *.jpg)"
    assert run(filters={"이미지": ["png"], "문서": ["txt"]}) == (
        "이미지 (*.png);;문서 (*.txt)"
    )
    assert run(filters=["*.png"]) == "PNG 파일 (*.png)"     # 설명은 자동으로 붙는다
    # 이미 Qt 문자열이면 그대로 (예전 코드가 그대로 돈다)
    assert run(filters="CSV (*.csv);;모든 파일 (*)") == "CSV (*.csv);;모든 파일 (*)"
    assert run() == ""                                   # 필터 없음

    # "모든 파일" 은 기본으로 붙이지 않는다(위젯과 반대)
    assert run(filters=[("CSV", ["csv"])]) == "CSV (*.csv)"
    assert run(filters=[("CSV", ["csv"])], add_all_files_filter=True) == (
        "CSV (*.csv);;모든 파일 (*)"
    )

    # 예전 이름 name_filter 도 그대로 받는다 (둘 다 주면 filters 가 이긴다)
    assert run(name_filter=[("CSV", ["csv"])]) == "CSV (*.csv)"
    assert run(filters=[("A", ["a"])], name_filter=[("B", ["b"])]) == "A (*.a)"


def test_widget_filter_not_double_appended(qapp, monkeypatch):
    """위젯이 만든 필터가 다이얼로그를 거치며 "모든 파일" 이 두 번 붙지 않는다."""
    seen = {}
    monkeypatch.setattr(
        dialog_module,
        "_run_dialog",
        lambda parent, mode, caption, directory, nf, *a: (seen.update(nf=nf), ([], ""))[1],
    )

    edit = FilePathEdit(mode="open_file", filters=[("CSV", ["csv"])])
    assert edit.name_filter() == "CSV (*.csv);;모든 파일 (*)"
    edit.browse()
    assert seen["nf"] == "CSV (*.csv);;모든 파일 (*)"     # 그대로 한 번만


def test_settings_key_keeps_start_dir_per_purpose(qapp, tmp_path, monkeypatch):
    """settings_key 마다 마지막에 쓰던 폴더를 따로 기억한다."""
    from custom_file_dialog import exec_file_dialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    csv_dir = tmp_path / "입력csv"
    out_dir = tmp_path / "결과"
    csv_dir.mkdir()
    out_dir.mkdir()
    (csv_dir / "a.csv").write_text("x")
    (out_dir / "r.json").write_text("x")

    seen, result = _dialog_start_dirs(monkeypatch)

    # 기억이 없으면 현재 작업 디렉터리에서 연다
    result["paths"] = [str(csv_dir / "a.csv")]
    exec_file_dialog(mode="open_file", settings_key="입력csv")
    assert seen[-1] == os.getcwd()

    result["paths"] = [str(out_dir / "r.json")]
    exec_file_dialog(mode="save_file", settings_key="결과저장")
    assert seen[-1] == os.getcwd()

    # 다시 열면 각자 자기가 마지막에 쓰던 폴더에서 연다
    result["paths"] = []
    exec_file_dialog(mode="open_file", settings_key="입력csv")
    assert seen[-1] == str(csv_dir)
    exec_file_dialog(mode="save_file", settings_key="결과저장")
    assert seen[-1] == str(out_dir)

    # 서로 섞이지 않는다
    assert last_dir("입력csv") == str(csv_dir)
    assert last_dir("결과저장") == str(out_dir)
    assert last_dir("한번도안쓴용도") is None


def test_settings_key_off_by_default(qapp, tmp_path, monkeypatch):
    """settings_key 를 주지 않으면 아무것도 기억하지 않는다."""
    from custom_file_dialog import exec_file_dialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)
    target = tmp_path / "a.csv"
    target.write_text("x")

    seen, result = _dialog_start_dirs(monkeypatch)
    result["paths"] = [str(target)]

    exec_file_dialog(mode="open_file")
    assert seen[-1] == ""                       # 시작 폴더를 정하지 않는다
    assert last_dir("") is None


def test_settings_key_respects_explicit_directory(qapp, tmp_path, monkeypatch):
    """directory 를 직접 주면 그쪽이 우선이고, 기억은 그래도 갱신된다."""
    from custom_file_dialog import exec_file_dialog, last_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)
    first = tmp_path / "처음"
    forced = tmp_path / "강제"
    first.mkdir()
    forced.mkdir()
    (first / "a.csv").write_text("x")
    (forced / "b.csv").write_text("x")

    seen, result = _dialog_start_dirs(monkeypatch)
    result["paths"] = [str(first / "a.csv")]
    exec_file_dialog(mode="open_file", settings_key="용도")
    assert last_dir("용도") == str(first)

    # 기억이 있어도 directory 가 이긴다
    result["paths"] = [str(forced / "b.csv")]
    exec_file_dialog(mode="open_file", settings_key="용도", directory=str(forced))
    assert seen[-1] == str(forced)
    assert last_dir("용도") == str(forced)      # 기억은 갱신된다


def test_settings_key_falls_back_when_dir_is_gone(qapp, tmp_path, monkeypatch):
    """기억해 둔 폴더가 사라졌으면 안전한 곳으로 대체한다."""
    from custom_file_dialog import exec_file_dialog, remember_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    gone = tmp_path / "사라질폴더"
    gone.mkdir()
    remember_dir("용도", str(gone))
    gone.rmdir()

    seen, _result = _dialog_start_dirs(monkeypatch)
    exec_file_dialog(mode="open_file", settings_key="용도")
    assert seen[-1] == os.getcwd()


def test_settings_key_shares_store_with_widget(qapp, tmp_path, monkeypatch):
    """같은 settings_key 면 위젯과 다이얼로그가 기억을 주고받는다."""
    from custom_file_dialog import exec_file_dialog, last_dir, remember_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    from_dialog = tmp_path / "다이얼로그"
    from_widget = tmp_path / "위젯"
    from_dialog.mkdir()
    from_widget.mkdir()
    (from_dialog / "a.csv").write_text("x")
    (from_widget / "b.csv").write_text("x")

    # 다이얼로그가 고른 것을 위젯이 이어받는다
    _seen, result = _dialog_start_dirs(monkeypatch)
    result["paths"] = [str(from_dialog / "a.csv")]
    exec_file_dialog(mode="open_file", settings_key="공용")

    edit = FilePathEdit(mode="open_file", settings_key="공용")
    assert edit._start_dir_now() == str(from_dialog)

    # 반대로 위젯이 고른 것을 다이얼로그가 이어받는다
    remember_dir("공용", str(from_widget / "b.csv"))
    assert last_dir("공용") == str(from_widget)


def test_remember_dir_helpers(qapp, tmp_path, monkeypatch):
    """remember_dir 는 파일이면 그 파일이 있는 폴더를 기억한다."""
    from custom_file_dialog import last_dir, remember_dir

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(history_module, "default_settings", lambda: settings)

    target = tmp_path / "a.csv"
    target.write_text("x")

    assert remember_dir("k", str(target)) == str(tmp_path)      # 파일 -> 그 폴더
    assert last_dir("k") == str(tmp_path)
    assert remember_dir("k", str(tmp_path)) == str(tmp_path)    # 폴더 -> 그대로

    # 빈 값은 아무 일도 하지 않는다
    assert remember_dir("k", "") is None
    assert remember_dir("", str(target)) is None
    assert last_dir("") is None
    assert last_dir(None) is None
    assert last_dir("k") == str(tmp_path)                       # 그대로 남아 있다


def test_memory_history_without_key():
    """key 가 없으면 QSettings 를 건드리지 않고 메모리에만 남는다."""
    history = PathHistory(key=None, max_items=2)
    history.add("/a")
    history.add("/b")
    history.add("/c")
    assert history.items() == ["/c", "/b"]



def test_exec_file_dialog_cleans_up_dialog(qapp, tmp_path, monkeypatch):
    """부를 때마다 다이얼로그 정리가 예약된다(파일시스템 감시 누수 방지).

    실제 삭제(sendPostedEvents 로 지연 삭제 강제 처리)는 다른 테스트가 큐에
    남긴 삭제까지 한꺼번에 터뜨려 스위트를 오염시키므로, 여기서는 deleteLater
    가 불렸는지만 본다. 실제 삭제까지 이어지는 것은 Qt 의 계약이다.
    """
    from custom_file_dialog import CustomFileDialog

    store = FavoritesStore(base_dir=str(tmp_path / "favorites"))
    design, _report, _output = _make_tree(tmp_path)
    store.add("설계", design)

    released = []
    original = CustomFileDialog.deleteLater
    monkeypatch.setattr(
        CustomFileDialog,
        "deleteLater",
        lambda self: (released.append(self), original(self))[1],
    )
    monkeypatch.setattr(dialog_module, "exec_dialog", lambda d: 0)   # 바로 취소

    for _ in range(3):
        dialog_module.exec_file_dialog(mode="open_file", favorites=store)
    assert len(released) == 3

    # 확인하고 닫아도(결과를 읽은 뒤에) 정리가 예약된다
    monkeypatch.setattr(dialog_module, "exec_dialog", lambda d: 1)
    paths, _chosen = dialog_module.exec_file_dialog(mode="open_file", favorites=store)
    assert len(released) == 4


def test_false_stores_do_not_force_instance_dialog(qapp, monkeypatch):
    """favorites=False / recent=False 는 "안 씀"이므로 정적 경로 그대로."""
    seen = []
    monkeypatch.setattr(
        dialog_module, "_run_dialog", lambda *a: (seen.append(a), ([], ""))[1]
    )
    dialog_module.exec_file_dialog(mode="open_file", favorites=False, recent=False)
    assert seen                              # 정적 메서드 경로를 탔다


def test_remember_dir_keeps_widget_history(qapp, tmp_path):
    """settings_key 로 시작 위치만 기록해도 위젯의 최근 목록이 잘리지 않는다.

    같은 키를 위젯은 history=30 으로, 헬퍼는 기본 크기로 쓴다. 마지막 폴더
    기록이 최근 목록까지 다시 쓰면 작은 쪽 기준으로 잘려 나갔었다.
    """
    from custom_file_dialog import last_dir, remember_dir

    ini = str(tmp_path / "s.ini")
    make = lambda: QSettings(ini, QSettings.Format.IniFormat)  # noqa: E731

    widget_side = PathHistory(key="공용", max_items=30, settings=make())
    for i in range(15):
        widget_side.add("/data/%02d.csv" % i)

    target = tmp_path / "폴더"
    target.mkdir()
    remember_dir("공용", str(target), settings=make())

    assert last_dir("공용", settings=make()) == str(target)
    reloaded = PathHistory(key="공용", max_items=30, settings=make())
    assert len(reloaded.items()) == 15       # 목록은 그대로


def test_custom_file_dialog_records_recent(qapp, tmp_path):
    """위젯 없이 클래스로 바로 띄워도 고른 파일이 최근 파일에 쌓인다."""
    from custom_file_dialog import CustomFileDialog

    recent = RecentStore(base_dir=str(tmp_path / "recent"), max_items=10)
    design, report, _output = _make_tree(tmp_path)

    dialog = CustomFileDialog(
        None, mode="open_file", directory=os.path.dirname(design), recent=recent
    )
    dialog.selectFile(design)
    _close_soon(dialog)
    assert _run(dialog)
    assert recent.items() == [design]

    # 취소하면 기록하지 않는다
    cancelled = CustomFileDialog(
        None, mode="open_file", directory=os.path.dirname(report), recent=recent
    )
    cancelled.selectFile(report)
    _close_soon(cancelled, accepted=False)
    _run(cancelled)
    assert recent.items() == [design]
