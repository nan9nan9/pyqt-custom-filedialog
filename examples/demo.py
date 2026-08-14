"""CustomFileDialog 데모 — 모든 기능을 켠 다이얼로그 하나.

실행::

    python examples/demo.py

버튼을 누르면 **이 라이브러리의 기능이 전부 켜진 파일 다이얼로그**가 뜬다.
기능마다 상자를 따로 두지 않고, 실제로 앱에 붙였을 때와 같은 모습 하나만 보인다.

다이얼로그에서 확인할 것

    사이드바   🏠 홈 아이콘 · "현재 위치" 이름 · 🕘 최근 파일 · ★ 즐겨찾기 분류
    우클릭     파일 목록에서 "즐겨찾기에 추가 ▸" 와 "경로 복사",
               분류 안에서는 "'설계'에서 제거" (Qt 기본 "Delete" 대신)
    링크 추적  분류에서 항목을 고르면 Look in 에 원본 경로가 뜨고,
               "상위 폴더(↑)" 도 링크 창고가 아니라 원본이 있는 폴더로 간다
    시작 위치  모드마다 다른 settings_key 라 각자 마지막에 쓰던 폴더에서 열린다
    안전       차단 경로는 자동완성이 나열하지 않고 들어가지도 못한다

아래 체크박스로 기능을 껐다 켜서 **차이**를 볼 수 있다. 창을 열면 데모용 임시
폴더 트리가 만들어지고, 닫을 때 함께 지워진다.
"""

import os
import shutil
import sys
import tempfile
import time

# 소스 그대로 실행할 수 있도록 src 경로 추가 (pip install 한 경우엔 불필요)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _pick_cursor_theme():
    """커서 테마가 없는 환경(WSL 등)에서 창 위의 마우스 커서가 사라지는 것을 막는다.

    Qt 는 자기 창의 커서 이미지를 직접 그리는데, X 커서 테마가 하나도 없으면
    빈 커서가 되어 **Qt 창 위에서만** 커서가 안 보인다. 시스템/사용자 설정은
    건드리지 않고 이 프로세스에만 테마를 지정한다.
    """
    if os.environ.get("XCURSOR_THEME"):
        return                                  # 사용자가 이미 정했으면 존중
    bases = [
        os.path.expanduser("~/.local/share/icons"),
        os.path.expanduser("~/.icons"),
        "/usr/share/icons",
    ]
    themes = [
        (base, name)
        for base in bases
        if os.path.isdir(base)
        for name in sorted(os.listdir(base))
        if os.path.isdir(os.path.join(base, name, "cursors"))
    ]
    # 얹을 게 없거나("default" 포함) 기본 테마가 멀쩡하면 Qt 가 알아서 한다
    if themes and not any(name == "default" for _base, name in themes):
        os.environ["XCURSOR_THEME"] = themes[0][1]


_pick_cursor_theme()        # QApplication 보다 먼저 정해져야 한다

from qtpy.QtCore import Qt  # noqa: E402
from qtpy.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from custom_file_dialog import (  # noqa: E402
    CustomFileDialog,
    FavoritesStore,
    RecentStore,
    configure_settings,
    configure_storage,
    last_dir,
    safety,
)

# 즐겨찾기·최근 파일이 들어갈 뿌리 — 실제 앱처럼 ~/.config 아래를 쓴다.
# 데모 데이터는 임시 폴더를 가리키므로 닫을 때 뿌리째 지운다.
DEMO_STORAGE = os.path.expanduser("~/.config/custom-file-dialog-demo")

# ---------------------------------------------------------------- 클릭 측정
# 사이드바를 누를 때마다 **무엇이 얼마나 걸렸는지**를 아래 로그에 찍는다.
# 느리다는 보고를 받으면 이 줄을 그대로 알려 주면 된다.
#
# 우리 코드 쪽은 자주 불리는 셋을 감싸서 "몇 번 · 몇 ms" 로 센다. 나머지 시간은
# 전부 Qt 몫이다 — 폴더로 옮기고 목록을 채우는 일.
_TALLY = {}


def _counted(name, func):
    """호출 수와 걸린 시간을 :data:`_TALLY` 에 쌓는 껍데기."""

    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            slot = _TALLY.setdefault(name, [0, 0.0])
            slot[0] += 1
            slot[1] += time.perf_counter() - started

    return wrapper


def _install_counters():
    """자주 불리는 자리 몇 곳을 세도록 감싼다 (데모에서만)."""
    from custom_file_dialog import icons as icons_module
    from custom_file_dialog import places as places_module

    icons_module.CategoryIconProvider.icon = _counted(
        "icon()", icons_module.CategoryIconProvider.icon
    )
    places_module.Places.link_target = _counted(
        "link_target()", places_module.Places.link_target
    )
    safety.may_enter = _counted("may_enter()", safety.may_enter)


def _environment_line():
    """홈·저장소가 로컬인지 네트워크인지 한 줄로 — 측정값과 함께 붙이면 좋다."""
    from custom_file_dialog import mounts

    def where(path):
        mount = mounts.mount_for(path)
        if mount is None:
            return "?"
        if mount[1] in mounts.REMOTE_FSTYPES:
            return "원격(%s)" % mount[1]
        if mount[1] in mounts.AUTOMOUNT_FSTYPES:
            return "automount"
        return "로컬(%s)" % mount[1]

    return "[환경] 홈 %s · 저장소 %s · 원격 마운트 %d개 · automount %d개" % (
        where(os.path.expanduser("~")),
        where(DEMO_STORAGE),
        sum(1 for m in mounts.iter_mounts() if m[1] in mounts.REMOTE_FSTYPES),
        sum(1 for m in mounts.iter_mounts() if m[1] in mounts.AUTOMOUNT_FSTYPES),
    )


def _watch_sidebar(dialog, append):
    """사이드바 클릭 한 번을 단계별로 재서 로그에 한 줄씩 남긴다.

    누른 순간부터 재고, Qt 가 이동을 알린 시점(``directoryEntered``)과 목록이
    더 이상 늘지 않는 시점을 나눠 찍는다. 목록 채우기는 Qt 가 다른 스레드에서
    하므로 마지막 항목이 들어온 뒤 조금 기다렸다가 "끝"으로 본다.
    """
    from qtpy.QtCore import QEvent, QObject, QTimer
    from qtpy.QtWidgets import QListView

    sidebar = dialog.findChild(QListView, "sidebar")
    view = dialog.findChild(QListView, "listView")
    if sidebar is None or view is None:
        return None

    state = {"name": None, "press": 0.0, "entered": 0.0, "last": 0.0, "timer": None}

    def report():
        if state["name"] is None:
            return
        # **마지막 변화 시각**까지만 센다 — 아래 settle() 이 조용해질 때까지
        # 기다린 시간은 측정값이 아니라 판정에 쓴 여유다.
        done = state["last"]
        move = (state["entered"] - state["press"]) if state["entered"] else 0.0
        fill = max(0.0, done - state["entered"]) if state["entered"] else 0.0
        ours = sum(spent for _n, spent in _TALLY.values())
        detail = " · ".join(
            "%s %d회 %.1fms" % (name, count, spent * 1000)
            for name, (count, spent) in sorted(_TALLY.items())
        ) or "(우리 코드 호출 없음)"
        append("[사이드바] %s -> %s" % (state["name"], dialog.directory().absolutePath()))
        append("    전체 %.0fms | 폴더 이동 %.0fms | 목록 채움 %.0fms | 우리 코드 %.1fms"
               % ((done - state["press"]) * 1000, move * 1000, fill * 1000, ours * 1000))
        append("    %s" % detail)
        state["name"] = None

    def settle():
        """마지막 변화 뒤 250ms 동안 조용하면 다 채워진 것으로 본다."""
        state["last"] = time.perf_counter()
        if state["timer"] is not None:
            state["timer"].stop()
        timer = state["timer"] = QTimer(dialog)
        timer.setSingleShot(True)
        timer.timeout.connect(report)
        timer.start(250)

    class Press(QObject):
        def eventFilter(self, obj, event):      # noqa: N802 (Qt 시그니처)
            if event.type() == QEvent.Type.MouseButtonPress:
                index = sidebar.indexAt(event.pos())
                if index.isValid():
                    _TALLY.clear()
                    state.update(name=index.data(), press=time.perf_counter(),
                                 entered=0.0)
            return False

    watcher = Press(dialog)
    sidebar.viewport().installEventFilter(watcher)
    dialog.directoryEntered.connect(
        lambda _p: (state.update(entered=time.perf_counter()), settle())
    )
    model = view.model()
    if model is not None:
        model.rowsInserted.connect(lambda *_a: settle())
    return watcher


# 버튼 하나 = 모드 하나. settings_key 를 따로 주어 각자 시작 위치를 기억한다.
MODES = [
    ("open_file",  "파일 열기",    [("CSV", ["csv"]), ("문서", ["md", "txt"])], None),
    ("open_files", "여러 개 열기", [("이미지", ["png", "jpg"])],                 None),
    ("save_file",  "저장하기",     [("CSV", ["csv"])],                           "csv"),
    ("directory",  "폴더 선택",    None,                                         None),
]


def build_playground():
    """데모용 폴더 트리와 저장소를 만든다.

    ``/user`` 처럼 나열하면 안 되는 자리도 흉내 내어, 안전 기능을 켜고 끄며
    차이를 볼 수 있게 한다.
    """
    root = tempfile.mkdtemp(prefix="cfd-demo-")
    work = os.path.join(root, "작업")

    for name, files in (
        ("설계", ["도면.csv", "치수.csv"]),
        ("보고서", ["1월.md", "2월.md"]),
        ("이미지", ["로고.png", "배경.jpg"]),
    ):
        folder = os.path.join(work, name)
        os.makedirs(folder)
        for file_name in files:
            open(os.path.join(folder, file_name), "w", encoding="utf-8").close()

    # 나열하는 것만으로 주저앉는 자리 흉내 (하위 경로는 멀쩡하다)
    danger = os.path.join(root, "user")
    for name in ("myaccount", "alice", "bob"):
        os.makedirs(os.path.join(danger, name))

    # 지난 실행이 비정상 종료로 남긴 데모 저장소가 있으면 비우고 시작한다
    # (데모 항목은 그 실행의 임시 폴더를 가리키므로 남겨 둘 값어치가 없다)
    shutil.rmtree(DEMO_STORAGE, ignore_errors=True)
    configure_storage(DEMO_STORAGE)
    favorites = FavoritesStore()            # -> <DEMO_STORAGE>/favorites
    recent = RecentStore(max_items=10)      # -> <DEMO_STORAGE>/recent

    # 처음부터 뭔가 들어 있어야 사이드바를 바로 확인할 수 있다
    favorites.add("설계", os.path.join(work, "설계", "도면.csv"))
    favorites.add("보고서", os.path.join(work, "보고서", "1월.md"))
    favorites.add("폴더모음", os.path.join(work, "이미지"))
    recent.record(os.path.join(work, "설계", "치수.csv"))

    return {
        "root": root,
        "work": work,
        "danger": danger,
        "favorites": favorites,
        "recent": recent,
    }


def build_window(tree, append):
    """데모 창의 알맹이를 만든다. ``(위젯, 갱신함수)`` 를 돌려준다."""
    layout = QVBoxLayout()
    layout.setSpacing(10)

    hint = QLabel(
        "버튼을 누르면 아래 기능이 <b>모두 켜진</b> 다이얼로그가 뜹니다.<br>"
        "사이드바의 🏠 홈 아이콘과 \"현재 위치\", ★ 분류를 보고, 파일 목록과 "
        "분류 안에서 <b>우클릭</b>해 보세요.<br>"
        "분류 안의 항목을 고른 뒤 <b>상위 폴더(↑)</b> 를 누르면 링크 창고가 아니라 "
        "원본이 있는 폴더로 갑니다."
    )
    hint.setWordWrap(True)
    hint.setTextFormat(Qt.TextFormat.RichText)
    hint.setStyleSheet("color: gray;")
    layout.addWidget(hint)

    button_box = QGroupBox("다이얼로그 열기")
    button_row = QHBoxLayout(button_box)
    layout.addWidget(button_box)

    result = QLabel("(아직 고른 것 없음)")
    result.setObjectName("demo_result")
    result.setWordWrap(True)
    result.setStyleSheet("color: #1565c0; font-weight: bold;")
    layout.addWidget(result)

    switch_box = QGroupBox("켜져 있는 기능 — 꺼서 차이를 볼 수 있습니다")
    switches = QGridLayout(switch_box)
    layout.addWidget(switch_box)

    checks = {}
    for row, (key, text, tip) in enumerate([
        ("favorites", "★ 즐겨찾기 — 분류를 사이드바에",
         "FavoritesStore 의 분류가 사이드바에 별표로 올라갑니다.\n"
         "분류 안에서 우클릭하면 \"'설계'에서 제거\" 가 나옵니다."),
        ("recent", "🕘 최근 파일 — 고른 파일을 자동으로 모음",
         "RecentStore. 고를 때마다 쌓이고 사이드바 항목 하나로 보입니다."),
        ("icon", "🏠 홈 아이콘 · \"현재 위치\" 이름",
         "끄면 홈이 Qt 기본 폴더 아이콘으로 돌아갑니다.\n"
         "(\"현재 위치\" 이름은 아이콘 설정과 무관하게 유지됩니다.)"),
        ("memory", "📌 settings_key — 모드마다 시작 위치를 따로 기억",
         "끄면 Qt 전역 lastVisited 하나를 모든 모드가 공유합니다."),
        ("safety", "🛡 안전 — 차단 경로 · 자동완성 최소 깊이",
         "guarded_roots 로 흉내낸 user 폴더를 막고,\n"
         "min_depth 로 얕은 자리는 자동완성이 나열하지 않게 합니다."),
    ]):
        box = QCheckBox(text)
        box.setChecked(True)
        box.setToolTip(tip)
        switches.addWidget(box, row, 0)
        checks[key] = box

    state = QLabel()
    state.setObjectName("demo_state")
    state.setWordWrap(True)
    state.setStyleSheet("color: gray; font-family: monospace;")
    layout.addWidget(state)

    # ------------------------------------------------------------- 동작
    def apply_safety():
        """안전 설정은 전역이라 다이얼로그를 **만들기 전에** 적용해야 한다."""
        if checks["safety"].isChecked():
            safety.configure(guarded_roots=[tree["danger"]], min_depth=2)
        else:
            safety.reset()

    def refresh():
        remembered = []
        for mode, label, _filters, _suffix in MODES:
            where = last_dir("demo_%s" % mode) if checks["memory"].isChecked() else None
            remembered.append("%s=%s" % (label, os.path.basename(where) if where else "-"))
        state.setText(
            "즐겨찾기 분류 %s · 최근 파일 %d개\n"
            "차단 경로 %s · 자동완성 최소 깊이 %s\n"
            "기억해 둔 시작 위치  %s"
            % (
                tree["favorites"].categories() or "(없음)",
                len(tree["recent"].items()),
                [os.path.basename(p) for p in safety.guarded_roots()] or "(없음)",
                safety.min_depth() or "(제한 없음)",
                "   ".join(remembered),
            )
        )

    def open_dialog(mode, label, filters, suffix):
        apply_safety()                       # 다이얼로그를 만들기 전에
        key = "demo_%s" % mode
        remembers = checks["memory"].isChecked()
        # 시작 폴더는 **생성자에** 줘야 한다. 만든 뒤에 setDirectory() 를 부르면
        # 사이드바의 "현재 위치" 항목이 그 전 폴더를 가리킨 채로 남는다.
        # (기억해 둔 곳이 있으면 settings_key 가 알아서 잡아 준다)
        dialog = CustomFileDialog(
            state.window(),
            mode=mode,
            caption="%s — 모든 기능 켜짐" % label,
            directory=None if (remembers and last_dir(key)) else tree["work"],
            filters=filters,
            default_suffix=suffix,
            favorites=tree["favorites"] if checks["favorites"].isChecked() else None,
            recent=tree["recent"] if checks["recent"].isChecked() else None,
            favorites_icon=checks["icon"].isChecked(),
            settings_key=key if remembers else None,
        )

        watcher = _watch_sidebar(dialog, append)     # 사이드바 클릭 측정
        append("[열기] %s — 시작 폴더 %s" % (label, dialog.directory().absolutePath()))
        run = getattr(dialog, "exec_", None) or dialog.exec
        if not run():
            append("[취소] %s" % label)
            refresh()
            return

        paths = dialog.selectedFiles()       # 즐겨찾기 링크는 원본으로 복원된다
        result.setText("%s → %s" % (label, "\n     ".join(paths) or "(없음)"))
        append("[선택] %s -> %s" % (label, paths))
        refresh()      # 최근 파일 기록은 CustomFileDialog 가 스스로 한다

    for mode, label, filters, suffix in MODES:
        button = QPushButton(label)
        button.setObjectName("demo_btn_%s" % mode)
        button.clicked.connect(
            lambda _c=False, m=mode, t=label, f=filters, s=suffix: open_dialog(m, t, f, s)
        )
        button_row.addWidget(button)
    button_row.addStretch(1)

    for box in checks.values():
        box.toggled.connect(lambda _c: (apply_safety(), refresh()))

    panel = QWidget()
    panel.setLayout(layout)
    return panel, apply_safety, refresh


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("myaccount")
    app.setApplicationName("custom-file-dialog-demo")
    # settings_key 를 쓰는 자리들이 공유할 QSettings 정보
    configure_settings("myaccount", "custom-file-dialog-demo")

    _install_counters()          # 사이드바 클릭 측정용 (데모에서만)
    tree = build_playground()

    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setMaximumBlockCount(300)
    log.appendPlainText(_environment_line())
    log.appendPlainText(
        "사이드바 항목을 누를 때마다 걸린 시간이 여기 찍힌다"
        " (느리면 이 줄들을 그대로 알려 주면 된다)."
    )

    panel, apply_safety, refresh = build_window(tree, log.appendPlainText)

    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.addWidget(panel)
    splitter.addWidget(log)
    splitter.setStretchFactor(0, 3)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([460, 150])

    holder = QWidget()
    holder_layout = QVBoxLayout(holder)
    holder_layout.setContentsMargins(8, 8, 8, 8)
    holder_layout.addWidget(splitter)

    window = QMainWindow()
    window.setWindowTitle("CustomFileDialog 데모")
    window.resize(780, 640)
    window.setCentralWidget(holder)

    apply_safety()
    refresh()
    log.appendPlainText("데모 폴더: %s" % tree["root"])
    log.appendPlainText("즐겨찾기·최근 파일 저장소: %s" % DEMO_STORAGE)
    log.appendPlainText(
        "차단 경로 흉내: %s (하위 myaccount/alice/bob 은 정상)" % tree["danger"]
    )
    log.appendPlainText("버튼을 눌러 다이얼로그를 열어 보세요.")

    def cleanup():
        safety.reset()
        shutil.rmtree(tree["root"], ignore_errors=True)
        # 데모 항목은 지워진 임시 폴더를 가리키므로 저장소도 함께 지운다
        shutil.rmtree(DEMO_STORAGE, ignore_errors=True)
        configure_storage(None)

    app.aboutToQuit.connect(cleanup)

    window.show()
    sys.exit(app.exec_() if hasattr(app, "exec_") else app.exec())


if __name__ == "__main__":
    main()
