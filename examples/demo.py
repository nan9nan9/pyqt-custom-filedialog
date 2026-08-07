"""FilePathEdit / FilePathForm 데모.

실행::

    python examples/demo.py

기능별 상자를 두 단으로 한 화면에 펼쳐 놓았다(넘치면 스크롤).

    왼쪽  선택 모드  : open_file / open_files / save_file / directory 네 가지
          필터       : filters 를 지정하는 여러 형태와 조립된 Qt 필터 문자열
          유효성     : required / must_exist / validate 조합에 따른 표시 변화
          히스토리   : 최근 경로 드롭다운(▾)과 QSettings 저장
          폼         : FilePathForm 으로 여러 줄을 묶어 값 꺼내기

    오른쪽 사이드바  : 다이얼로그 왼쪽 즐겨찾기 목록 교체
          즐겨찾기·최근 : 파일/폴더를 분류별로 모으고, 최근에 고른 파일도 함께
                          사이드바에 띄워 한 위젯에서 같이 쓰기
          안전       : 나열하면 안 되는 경로 차단(guarded_roots)과
                       응답 없는 네트워크 경로 방어(path_timeout)

맨 위 공통 옵션(네이티브 다이얼로그 / 드래그&드롭)은 모든 위젯에 한꺼번에
적용되고, 맨 아래 로그는 경계를 끌어 높이를 조절할 수 있다.
"""

import os
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
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from file_dialog_widget import (  # noqa: E402
    safety,
    GuardedFileSystemModel,
    FavoritesError,
    FavoritesStore,
    FilePathEdit,
    FilePathForm,
    configure_settings,
    current_sidebar_urls,
    RecentStore,
    clock_icon,
    exec_file_dialog,
    star_icon,
)


def _hint(text):
    """각 상자 위에 놓는 회색 설명 문구."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


def _readonly(text):
    """보여 주기만 하는 회색 입력칸. 칸보다 긴 경로는 앞쪽이 보이게 맞춘다."""
    edit = QLineEdit(text)
    edit.setReadOnly(True)
    edit.setStyleSheet("color: gray;")
    edit.setCursorPosition(0)
    edit.setToolTip(text)
    return edit


# ------------------------------------------------------------- 선택 모드 상자
def build_modes_section(track, append):
    section = QGroupBox("선택 모드")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "모드에 따라 열리는 QFileDialog 가 달라집니다. "
            "파일 탐색기에서 끌어다 놓아도 채워집니다."
        )
    )

    # 1) 파일 하나 열기
    open_edit = track(
        FilePathEdit(
            mode="open_file",
            label="파일 열기:",
            filters=[("텍스트", ["txt", "md"]), ("파이썬", ["py"])],
        )
    )
    open_edit.set_tooltip("열어서 읽을 파일을 고릅니다.")
    layout.addWidget(open_edit)

    # 2) 파일 여러 개 — 값은 "; " 로 이어져 한 줄에 표시된다
    multi_edit = track(
        FilePathEdit(
            mode="open_files",
            label="여러 파일:",
            filters=[("이미지", ["png", "jpg", "jpeg", "bmp"])],
        )
    )
    layout.addWidget(multi_edit)

    # 3) 저장 — 아직 없는 파일이 정상이므로 유효성 기준이 다르다
    #    (상위 폴더만 있으면 OK, 확장자를 안 쓰면 default_suffix 가 붙는다)
    save_edit = track(
        FilePathEdit(
            mode="save_file",
            label="다른 이름으로 저장:",
            filters=[("CSV", ["csv"]), ("JSON", ["json"])],
            default_suffix="csv",
        )
    )
    layout.addWidget(save_edit)

    # 4) 폴더 — 아이콘 버튼 예시
    dir_edit = track(
        FilePathEdit(
            mode="directory",
            label="작업 폴더:",
            button_icon=True,
            start_dir=os.path.expanduser("~"),
        )
    )
    layout.addWidget(dir_edit)

    open_edit.pathChanged.connect(lambda p: append("[모드] 파일 열기 -> %s" % p))
    multi_edit.pathsChanged.connect(
        lambda paths: append("[모드] 여러 파일 -> %d개 %s" % (len(paths), paths))
    )
    save_edit.browsed.connect(
        lambda paths: append("[모드] 저장 경로 확정 -> %s" % paths[0])
    )
    dir_edit.pathChanged.connect(lambda p: append("[모드] 작업 폴더 -> %s" % p))

    return section


# ----------------------------------------------------------------- 필터 상자
def build_filters_section(track, append):
    section = QGroupBox("필터")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "filters 는 아래 어떤 형태로 줘도 됩니다. 오른쪽은 실제로 "
            "QFileDialog 에 전달되는 Qt 필터 문자열(name_filter())입니다."
        )
    )

    samples = [
        ("(설명, 확장자 목록)", [("이미지", ["png", "jpg"])], True),
        ("(설명, 패턴 문자열)", [("로그", "*.log *.log.*")], True),
        ("dict", {"문서": ["txt", "md"], "표": ["csv", "xlsx"]}, True),
        ("패턴만 나열", ["*.py", "*.pyi"], True),
        ("Qt 필터 문자열 그대로", "백업 (*.bak);;모든 파일 (*)", True),
        ("모든 파일 자동 추가 끄기", [("CSV", ["csv"])], False),
    ]

    for title, filters, add_all in samples:
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)

        edit = track(
            FilePathEdit(
                mode="open_file", filters=filters, add_all_files_filter=add_all
            )
        )
        box_layout.addWidget(edit)

        # 조립 결과를 그대로 보여 준다(읽기 전용)
        box_layout.addWidget(_readonly(edit.name_filter() or "(필터 없음)"))

        layout.addWidget(box)

    append("[필터] 6가지 지정 방식을 조립해 두었습니다.")
    return section


# ---------------------------------------------------------------- 유효성 상자
def build_validation_section(track, append):
    section = QGroupBox("유효성")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "존재하지 않는 경로는 테두리가 빨갛게 되고 툴팁에 사유가 나옵니다. "
            "아래 체크박스로 판정 기준을 바꿔 보세요."
        )
    )

    edit = track(FilePathEdit(mode="open_file", label="검사할 경로:"))
    layout.addWidget(edit)

    status = QLabel()
    layout.addWidget(status)

    option_row = QHBoxLayout()
    required_check = QCheckBox("required (비어 있으면 오류)")
    must_exist_check = QCheckBox("must_exist (존재해야 유효)")
    must_exist_check.setChecked(True)
    validate_check = QCheckBox("validate (유효성 표시 자체)")
    validate_check.setChecked(True)
    for check in (required_check, must_exist_check, validate_check):
        option_row.addWidget(check)
    option_row.addStretch(1)
    layout.addLayout(option_row)

    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("모드 바꿔 보기:"))
    for mode_name, caption in (
        ("open_file", "파일 열기"),
        ("save_file", "저장"),
        ("directory", "폴더"),
    ):
        button = QPushButton(caption)
        # 모드를 바꾸면 must_exist 기본값도 함께 따라간다
        button.clicked.connect(lambda _c=False, m=mode_name: _switch_mode(edit, m))
        mode_row.addWidget(button)
    mode_row.addStretch(1)
    layout.addLayout(mode_row)

    def _switch_mode(target, mode_name):
        target.set_mode(mode_name)
        # 모드를 바꾸면 must_exist 기본값도 바뀌므로 체크박스를 맞춰 준다
        must_exist_check.setChecked(target.must_exist())
        append("[유효성] 모드 -> %s" % mode_name)
        refresh()

    def refresh():
        if edit.is_valid():
            status.setText("상태: 유효")
            status.setStyleSheet("color: #2e7d32;")
        else:
            status.setText("상태: 무효 — %s" % edit.invalid_reason())
            status.setStyleSheet("color: #e53935;")

    required_check.toggled.connect(edit.set_required)
    required_check.toggled.connect(lambda _c: refresh())
    must_exist_check.toggled.connect(edit.set_must_exist)
    must_exist_check.toggled.connect(lambda _c: refresh())
    validate_check.toggled.connect(edit.set_validate_enabled)
    validate_check.toggled.connect(lambda _c: refresh())
    edit.pathChanged.connect(lambda _p: refresh())
    edit.validityChanged.connect(
        lambda ok: append("[유효성] 상태 변경 -> %s" % ("유효" if ok else "무효"))
    )
    refresh()

    return section


# --------------------------------------------------------------- 사이드바 상자
def build_sidebar_section(track, append):
    section = QGroupBox("사이드바")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "다이얼로그 왼쪽 목록(기본: 홈 · 현재 위치 …)을 원하는 폴더로 "
            "통째로 교체합니다.\n"
            "주의 1) 네이티브 다이얼로그에서는 불가능해서, 지정하면 native 설정과 "
            "무관하게 Qt 자체 창으로 열립니다.\n"
            "주의 2) Qt 가 사이드바를 사용자 설정(~/.config/QtProject.conf)에 "
            "영구 저장하므로, 한 번 지정하면 다음 실행에도 남습니다."
        )
    )

    layout.addWidget(QLabel("사이드바에 넣을 경로 (한 줄에 하나, ~ 사용 가능):"))
    url_edit = QPlainTextEdit()
    url_edit.setPlainText("~\n%s\n/tmp" % os.getcwd())
    url_edit.setMaximumHeight(110)
    layout.addWidget(url_edit)

    button_row = QHBoxLayout()
    apply_btn = QPushButton("적용 (통째로 교체)")
    append_btn = QPushButton("현재 항목 뒤에 덧붙이기")
    load_btn = QPushButton("현재 항목 불러오기")
    off_btn = QPushButton("커스터마이즈 끄기")
    for button in (apply_btn, append_btn, load_btn, off_btn):
        button_row.addWidget(button)
    button_row.addStretch(1)
    layout.addLayout(button_row)

    edit = track(
        FilePathEdit(mode="open_file", label="여기서 열어 확인:")
    )
    layout.addWidget(edit)

    state = QLabel()
    layout.addWidget(state)

    def entered_paths():
        return [line.strip() for line in url_edit.toPlainText().splitlines() if line.strip()]

    def as_text(item):
        """QUrl 이든 문자열이든 사람이 읽을 경로 문자열로.

        ``file:`` (로컬 경로가 비는 URL)은 사이드바의 "Computer" 항목이다.
        """
        if hasattr(item, "toLocalFile"):
            return item.toLocalFile() or item.toString()
        return str(item)

    def refresh_state():
        urls = edit.sidebar_urls()
        if urls is None:
            state.setText("현재: 커스터마이즈 안 함 (저장된 사이드바 사용)")
            state.setStyleSheet("color: gray;")
        else:
            state.setText(
                "현재: %d개 — %s" % (len(urls), ", ".join(as_text(u) for u in urls))
            )
            state.setStyleSheet("color: #1565c0;")

    def on_apply():
        paths = entered_paths()
        edit.set_sidebar_urls(paths)
        append("[사이드바] 교체 -> %s" % paths)
        refresh_state()

    def on_append():
        # 기존 항목(QUrl 리스트) 뒤에 문자열 경로를 그대로 이어 붙여도 된다.
        # FilePathEdit 가 넘겨받은 목록을 to_urls() 로 알아서 변환한다.
        existing = current_sidebar_urls()
        added = entered_paths()
        edit.set_sidebar_urls(existing + added)
        append("[사이드바] 기존 %d개 + 새 %d개" % (len(existing), len(added)))
        refresh_state()

    def on_load():
        urls = current_sidebar_urls()
        url_edit.setPlainText("\n".join(as_text(u) for u in urls))
        append("[사이드바] 현재 항목 %d개를 불러왔습니다." % len(urls))

    def on_off():
        edit.set_sidebar_urls(None)
        append("[사이드바] 커스터마이즈 끔 (이미 저장된 항목은 남습니다)")
        refresh_state()

    apply_btn.clicked.connect(on_apply)
    append_btn.clicked.connect(on_append)
    load_btn.clicked.connect(on_load)
    off_btn.clicked.connect(on_off)
    refresh_state()

    return section

# ------------------------------------------------- 즐겨찾기 · 최근 파일 상자
def build_places_section(track, append):
    """즐겨찾기와 최근 파일을 한 위젯에서 함께 쓰는 상자.

    둘 다 "분류 폴더 + 심볼릭 링크" 라는 같은 방식이라 하나의 FilePathEdit 에
    같이 걸 수 있고, 그때 사이드바는
    ``홈 > 현재 위치 > 최근 파일 > 북마크 분류`` 순서가 된다.
    """
    section = QGroupBox("즐겨찾기 · 최근 파일")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "사이드바는 디렉터리만 받기 때문에(파일 URL 은 Qt 가 버립니다), "
            "분류마다 폴더를 만들고 그 안에 심볼릭 링크를 모읍니다. "
            "즐겨찾기는 ★ 별표, 최근 파일은 🕘 시계 아이콘이 붙습니다.\n"
            "고른 경로는 링크가 아니라 항상 원본으로 되돌아옵니다. "
            "사이드바에서 분류를 우클릭하면 분류 삭제(즐겨찾기) / 목록 비우기"
            "(최근 파일) 메뉴가 나옵니다."
        )
    )

    state = {"favorites": FavoritesStore(), "recent": RecentStore()}

    # ------------------------------------------------------ 공통 확인 영역
    edit = track(
        FilePathEdit(
            mode="open_file",
            label="여기서 열어 확인:",
            favorites=state["favorites"],
            recent_files=state["recent"],
        )
    )
    layout.addWidget(edit)

    order_label = QLabel()
    order_label.setObjectName("demo_sidebar_order")
    order_label.setWordWrap(True)
    order_label.setStyleSheet("color: #1565c0;")
    layout.addWidget(order_label)

    toggle_row = QHBoxLayout()
    fav_check = QCheckBox("즐겨찾기 사용")
    fav_check.setChecked(True)
    recent_check = QCheckBox("최근 파일 사용")
    recent_check.setChecked(True)
    for check in (fav_check, recent_check):
        toggle_row.addWidget(check)
    toggle_row.addStretch(1)
    layout.addLayout(toggle_row)

    # ------------------------------------------------------ 즐겨찾기 상자
    boxes = QHBoxLayout()

    fav_box = QGroupBox("★ 즐겨찾기")
    fav_layout = QVBoxLayout(fav_box)

    fav_base = _readonly(state["favorites"].base_dir)
    fav_layout.addWidget(fav_base)

    fav_add_row = QHBoxLayout()
    fav_add_row.addWidget(QLabel("분류:"))
    # 실제로 있는 분류를 보여 주고, 새 이름을 직접 입력할 수도 있게 한다.
    # (없는 분류 이름을 미리 채워 두면 "왜 이 분류가 있지?" 하고 헷갈린다)
    category_edit = QComboBox()
    category_edit.setEditable(True)
    category_edit.setMinimumWidth(140)
    category_edit.lineEdit().setPlaceholderText("분류 이름 (새로 입력 가능)")
    fav_add_row.addWidget(category_edit)
    add_file_btn = QPushButton("파일 등록")
    add_dir_btn = QPushButton("폴더 등록")
    fav_add_row.addWidget(add_file_btn)
    fav_add_row.addWidget(add_dir_btn)
    fav_add_row.addStretch(1)
    fav_layout.addLayout(fav_add_row)

    fav_list = QListWidget()
    fav_list.setMinimumHeight(140)
    fav_layout.addWidget(fav_list)

    fav_btn_row = QHBoxLayout()
    fav_remove_btn = QPushButton("선택 제거")
    fav_drop_btn = QPushButton("분류 삭제")
    fav_clear_btn = QPushButton("모두 비우기")
    fav_change_base_btn = QPushButton("위치 변경...")
    for button in (fav_remove_btn, fav_drop_btn, fav_clear_btn, fav_change_base_btn):
        fav_btn_row.addWidget(button)
    fav_btn_row.addStretch(1)
    fav_layout.addLayout(fav_btn_row)

    boxes.addWidget(fav_box, 1)

    # ------------------------------------------------------ 최근 파일 상자
    recent_box = QGroupBox("🕘 최근 파일")
    recent_layout = QVBoxLayout(recent_box)

    recent_layout.addWidget(_readonly(state["recent"].base_dir))

    recent_top = QHBoxLayout()
    recent_top.addWidget(QLabel("최대 개수:"))
    max_spin = QSpinBox()
    max_spin.setRange(0, 100)
    max_spin.setValue(state["recent"].max_items)
    recent_top.addWidget(max_spin)
    recent_top.addStretch(1)
    recent_layout.addLayout(recent_top)

    recent_list = QListWidget()
    recent_list.setMinimumHeight(140)
    recent_layout.addWidget(recent_list)

    recent_btn_row = QHBoxLayout()
    recent_clear_btn = QPushButton("목록 비우기")
    recent_refresh_btn = QPushButton("새로고침")
    recent_btn_row.addWidget(recent_clear_btn)
    recent_btn_row.addWidget(recent_refresh_btn)
    recent_btn_row.addStretch(1)
    recent_layout.addLayout(recent_btn_row)

    boxes.addWidget(recent_box, 1)
    layout.addLayout(boxes)

    # ------------------------------------------------------------- 동작
    def as_text(url):
        """QUrl 을 사람이 읽을 이름으로 (경로가 비면 Computer)."""
        path = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
        return os.path.basename(path.rstrip(os.sep)) or "Computer"

    def refresh_order():
        urls = edit.effective_sidebar_urls()
        if urls is None:
            order_label.setText("사이드바: 손대지 않음 (즐겨찾기·최근 파일 모두 꺼짐)")
        else:
            order_label.setText("사이드바 순서: " + "  ›  ".join(as_text(u) for u in urls))

    def refresh_categories():
        """분류 콤보를 실제 목록으로 맞춘다(입력 중인 텍스트는 지키면서)."""
        typed = category_edit.currentText()
        categories = state["favorites"].categories()
        category_edit.blockSignals(True)
        category_edit.clear()
        category_edit.addItems(categories)
        category_edit.setEditText(typed)
        category_edit.blockSignals(False)

    def refresh_favorites():
        fav_list.clear()
        store = state["favorites"]
        for category in store.categories():
            header = QListWidgetItem(star, category)
            header.setFlags(Qt.ItemFlag.NoItemFlags)    # 헤더는 선택 불가
            fav_list.addItem(header)
            for name, target in store.entries(category):
                item = QListWidgetItem("      %s  →  %s" % (name, target))
                item.setData(Qt.ItemDataRole.UserRole, (category, target))
                fav_list.addItem(item)
        if fav_list.count() == 0:
            fav_list.addItem("(등록된 즐겨찾기 없음)")

    def refresh_recent():
        recent_list.clear()
        items = state["recent"].items()
        for number, path in enumerate(items, 1):
            recent_list.addItem(QListWidgetItem(clock, "%2d. %s" % (number, path)))
        if not items:
            recent_list.addItem("(최근 파일 없음 — 위에서 파일을 골라 보세요)")
        recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def refresh_all():
        refresh_categories()
        refresh_favorites()
        refresh_recent()
        refresh_order()

    def current_category():
        return category_edit.currentText().strip() or "즐겨찾기"

    def pick_and_add(mode):
        paths, _chosen = exec_file_dialog(
            parent=section,
            mode=mode,
            caption="즐겨찾기에 등록할 대상 선택",
            directory=os.getcwd(),
        )
        if not paths:
            return
        try:
            state["favorites"].add(current_category(), paths[0])
        except FavoritesError as exc:
            append("[즐겨찾기] 실패: %s" % exc)
            return
        append("[즐겨찾기] %s 에 등록 -> %s" % (current_category(), paths[0]))
        refresh_all()

    def on_remove():
        item = fav_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not data:
            return
        category, target = data
        state["favorites"].remove(category, target)
        append("[즐겨찾기] 제거 -> %s" % target)
        refresh_all()

    def on_drop_category():
        state["favorites"].remove_category(current_category())
        append("[즐겨찾기] 분류 삭제 -> %s" % current_category())
        refresh_all()

    def on_clear_favorites():
        state["favorites"].clear()
        append("[즐겨찾기] 전체 비움 (원본 파일은 그대로입니다)")
        refresh_all()

    def on_change_base():
        paths, _chosen = exec_file_dialog(
            parent=section,
            mode="directory",
            caption="즐겨찾기를 저장할 폴더 선택",
            directory=os.path.dirname(state["favorites"].base_dir),
        )
        if not paths:
            return
        state["favorites"] = FavoritesStore(base_dir=paths[0])
        if fav_check.isChecked():
            edit.set_favorites(state["favorites"])
        fav_base.setText(state["favorites"].base_dir)
        fav_base.setCursorPosition(0)
        fav_base.setToolTip(state["favorites"].base_dir)
        append("[즐겨찾기] 저장 위치 -> %s" % state["favorites"].base_dir)
        refresh_all()

    def on_clear_recent():
        state["recent"].clear()
        append("[최근] 목록 비움 (원본 파일은 그대로입니다)")
        refresh_all()

    def on_max(value):
        state["recent"].set_max_items(value)
        append("[최근] 최대 개수 -> %d" % value)
        refresh_all()

    def on_toggle_favorites(checked):
        edit.set_favorites(state["favorites"] if checked else None)
        append("[즐겨찾기] 사용: %s" % checked)
        refresh_order()

    def on_toggle_recent(checked):
        edit.set_recent_files(state["recent"] if checked else False)
        append("[최근] 사용: %s" % checked)
        refresh_order()

    def on_browsed(paths):
        append("[선택] 원본 경로 -> %s" % paths[0])
        refresh_all()

    # 목록 표시에 쓸 아이콘 (다이얼로그 사이드바에 쓰이는 것과 같은 것)
    star = star_icon()
    clock = clock_icon()

    add_file_btn.clicked.connect(lambda: pick_and_add("open_file"))
    add_dir_btn.clicked.connect(lambda: pick_and_add("directory"))
    fav_remove_btn.clicked.connect(on_remove)
    fav_drop_btn.clicked.connect(on_drop_category)
    fav_clear_btn.clicked.connect(on_clear_favorites)
    fav_change_base_btn.clicked.connect(on_change_base)
    recent_clear_btn.clicked.connect(on_clear_recent)
    recent_refresh_btn.clicked.connect(refresh_all)
    max_spin.valueChanged.connect(on_max)
    fav_check.toggled.connect(on_toggle_favorites)
    recent_check.toggled.connect(on_toggle_recent)
    edit.browsed.connect(on_browsed)
    refresh_all()

    return section


# ------------------------------------------------------------------ 안전 상자
def build_safety_section(track, append):
    """멈추거나 시스템을 주저앉히는 경로를 미리 걸러 내는 기능 데모.

    실제 NFS 서버를 흉내 낼 수는 없으므로, ``guarded_roots`` 는 임시 폴더로
    ``/user`` 상황을 그대로 재현해 보여 주고, ``path_timeout`` 은 설정만 다룬다.
    """
    section = QGroupBox("안전 — 차단 경로 · 응답 없는 네트워크")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "/user 처럼 아래에 마운트가 잔뜩 달린 자리는 목록을 읽는 것만으로 전부 "
            "마운트되면서 시스템이 주저앉습니다. 입력창에 경로를 치는 순간 자동완성이 "
            "바로 그 일을 합니다.\n"
            "guarded_roots 에 등록하면 그 자리 자체는 열지 않고, 한 단계라도 아래인 "
            "경로만 쓰게 됩니다. 아래에서 켜고 끄며 차이를 확인해 보세요."
        )
    )

    # ------------------------------------------------- 흉내낼 /user 폴더 준비
    fake_root = os.path.join(tempfile.mkdtemp(prefix="fdw-demo-"), "user")
    os.makedirs(fake_root, exist_ok=True)
    for name in ("jekai", "alice", "bob"):
        os.makedirs(os.path.join(fake_root, name), exist_ok=True)
    os.makedirs(os.path.join(fake_root, "jekai", "proj"), exist_ok=True)

    guard_box = QGroupBox("차단 경로 (guarded_roots)")
    guard_layout = QVBoxLayout(guard_box)

    path_row = QHBoxLayout()
    path_row.addWidget(QLabel("흉내낼 경로:"))
    path_row.addWidget(_readonly(fake_root), 1)
    path_row.addWidget(QLabel("(하위 3개: jekai, alice, bob)"))
    guard_layout.addLayout(path_row)

    guard_check = QCheckBox("이 경로를 차단 경로로 등록")
    guard_layout.addWidget(guard_check)

    registered = QLabel()
    registered.setWordWrap(True)
    guard_layout.addWidget(registered)

    # 확인용 입력창 — 자동완성이 목록을 읽는지 직접 볼 수 있다
    probe_edit = track(
        FilePathEdit(mode="directory", label="여기에 쳐 보세요:", start_dir=fake_root)
    )
    guard_layout.addWidget(probe_edit)

    check_row = QHBoxLayout()
    check_root_btn = QPushButton("차단 경로 확인")
    check_child_btn = QPushButton("하위 경로 확인")
    check_row.addWidget(check_root_btn)
    check_row.addWidget(check_child_btn)
    check_row.addStretch(1)
    guard_layout.addLayout(check_row)

    result = QLabel()
    result.setObjectName("demo_guard_result")
    result.setWordWrap(True)
    guard_layout.addWidget(result)

    layout.addWidget(guard_box)

    # ---------------------------------------------- 응답 없는 네트워크 경로
    net_box = QGroupBox("응답 없는 네트워크 경로 (path_timeout)")
    net_layout = QVBoxLayout(net_box)

    net_layout.addWidget(
        _hint(
            "NFS 서버가 죽으면 stat() 이 중단 불가능 대기로 들어가 GUI 가 멈춥니다. "
            "마운트 판별 → 소켓 프로브 → 스레드+타임아웃 순으로 걸러 냅니다. "
            "기본으로 켜져 있고, 로컬 경로는 평소와 똑같이 처리합니다."
        )
    )

    timeout_row = QHBoxLayout()
    timeout_row.addWidget(QLabel("제한 시간(초):"))
    timeout_spin = QDoubleSpinBox()
    timeout_spin.setRange(0.1, 30.0)
    timeout_spin.setSingleStep(0.5)
    timeout_spin.setValue(probe_edit.path_timeout() or safety.DEFAULT_TIMEOUT)
    timeout_row.addWidget(timeout_spin)

    timeout_row.addWidget(QLabel("추가 프로브(호스트:포트):"))
    probes_edit = QLineEdit()
    probes_edit.setPlaceholderText("예: ldap.corp:389, nfs1.corp:2049")
    timeout_row.addWidget(probes_edit, 1)
    apply_btn = QPushButton("적용")
    timeout_row.addWidget(apply_btn)
    net_layout.addLayout(timeout_row)

    net_state = QLabel()
    net_state.setWordWrap(True)
    net_layout.addWidget(net_state)

    layout.addWidget(net_box)

    # ------------------------------------------------------------- 동작
    def listed_children(path, wait_ms=900):
        """자동완성 모델이 그 폴더를 **실제로 읽는지** 확인한다.

        QFileSystemModel 은 비동기라 한 번 읽은 결과를 캐시한다. 그래서 매번
        새 모델로 확인하고, ``directoryLoaded`` 가 올 때까지 기다린다.
        """
        model = GuardedFileSystemModel()
        model.setRootPath("")
        loaded = []
        model.directoryLoaded.connect(loaded.append)

        index = model.index(path)
        model.hasChildren(index)
        model.canFetchMore(index)
        model.fetchMore(index)

        deadline = time.time() + wait_ms / 1000.0
        while time.time() < deadline and not loaded:
            QApplication.processEvents()
            time.sleep(0.01)
        return bool(loaded), model.rowCount(model.index(path))

    def refresh_registered():
        roots = safety.guarded_roots()
        registered.setText("현재 등록: %s" % (roots or "(없음)"))
        registered.setStyleSheet("color: %s;" % ("#e53935" if roots else "gray"))

    def refresh_net():
        current = safety.settings()
        net_state.setText(
            "제한 시간 %.1f초 · 캐시 %.0f초 · 추가 프로브 %s"
            % (current["timeout"], current["ttl"], current["probes"] or "(없음)")
        )

    def on_guard(checked):
        safety.configure(guarded_roots=[fake_root] if checked else [])
        append("[안전] 차단 경로: %s" % (safety.guarded_roots() or "없음"))
        refresh_registered()
        show(fake_root, "차단 경로")

    def show(path, title):
        read, rows = listed_children(path)
        actual = len(os.listdir(path))
        result.setText(
            "%s  %s\n"
            "   is_guarded=%s · is_reachable=%s\n"
            "   폴더를 실제로 읽었나: %s (읽은 하위 %d개 / 실제 %d개)"
            % (
                title,
                path,
                safety.is_guarded(path),
                safety.is_reachable(path),
                "예" if read else "아니오 — 건드리지 않음",
                rows,
                actual,
            )
        )
        result.setStyleSheet(
            "color: %s;" % ("#e53935" if safety.is_guarded(path) else "#2e7d32")
        )
        append(
            "[안전] %s -> 읽음=%s (하위 %d/%d)" % (title, read, rows, actual)
        )

    def on_apply():
        probes = []
        for chunk in probes_edit.text().split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            host, _, port = chunk.partition(":")
            probes.append((host.strip(), int(port or 2049)))
        safety.configure(timeout=timeout_spin.value(), probes=probes)
        probe_edit.set_path_timeout(timeout_spin.value())
        append("[안전] 제한 시간 %.1f초, 프로브 %s" % (timeout_spin.value(), probes or "없음"))
        refresh_net()

    guard_check.toggled.connect(on_guard)
    check_root_btn.clicked.connect(lambda: show(fake_root, "차단 경로"))
    check_child_btn.clicked.connect(
        lambda: show(os.path.join(fake_root, "jekai"), "하위 경로")
    )
    apply_btn.clicked.connect(on_apply)
    refresh_registered()
    refresh_net()

    return section


# -------------------------------------------------------------- 히스토리 상자
def build_history_section(track, append):
    section = QGroupBox("히스토리")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "history 를 주면 입력창 오른쪽에 ▾ 드롭다운이 생겨 최근 경로를 "
            "고를 수 있습니다. settings_key 까지 주면 QSettings 에 저장되어 "
            "프로그램을 다시 켜도 유지되고, 다이얼로그도 직전 폴더에서 열립니다."
        )
    )

    saved = track(
        FilePathEdit(
            mode="open_file",
            label="저장됨 (settings_key):",
            history=10,
            settings_key="demo_open_file",
        )
    )
    layout.addWidget(saved)

    memory = track(
        FilePathEdit(
            mode="open_file",
            label="메모리만 (history=5):",
            history=5,
        )
    )
    layout.addWidget(memory)

    items = QLabel()
    items.setWordWrap(True)
    layout.addWidget(items)

    def refresh_items():
        saved_items = saved.history_items()
        memory_items = memory.history_items()
        items.setText(
            "저장됨: %s\n메모리: %s"
            % (saved_items or "(없음)", memory_items or "(없음)")
        )

    saved.browsed.connect(lambda paths: (append("[히스토리] 저장됨 <- %s" % paths[0]), refresh_items()))
    memory.browsed.connect(lambda paths: (append("[히스토리] 메모리 <- %s" % paths[0]), refresh_items()))
    refresh_items()

    return section


# ------------------------------------------------------------------- 폼 상자
def build_form_section(append):
    section = QGroupBox("폼 (FilePathForm)")
    layout = QVBoxLayout(section)

    layout.addWidget(
        _hint(
            "FilePathForm 은 여러 줄을 QFormLayout 으로 라벨 정렬해 묶고, "
            "key 로 값을 한 번에 꺼내게 해 줍니다."
        )
    )

    form = FilePathForm()
    form.add_path(
        "input",
        "입력 파일:",
        mode="open_file",
        filters=[("CSV", ["csv"]), ("엑셀", ["xlsx", "xls"])],
        required=True,
    )
    form.add_path("outdir", "출력 폴더:", mode="directory", required=True)
    form.add_path(
        "config",
        "설정 파일:",
        mode="open_file",
        filters=[("설정", ["ini", "toml", "json"])],
    )
    form.add_path("extra", "추가 파일들:", mode="open_files")

    # 경로가 아닌 위젯도 같은 정렬로 끼워 넣을 수 있다
    memo = QLineEdit()
    memo.setPlaceholderText("경로가 아닌 위젯도 같은 폼에 넣을 수 있습니다")
    form.add_row("메모:", memo)

    layout.addWidget(form)

    run_row = QHBoxLayout()
    run_btn = QPushButton("값 읽어오기")
    clear_btn = QPushButton("모두 비우기")
    run_row.addWidget(run_btn)
    run_row.addWidget(clear_btn)
    run_row.addStretch(1)
    valid_label = QLabel()
    run_row.addWidget(valid_label)
    layout.addLayout(run_row)

    def refresh_form_state():
        if form.is_valid():
            valid_label.setText("입력 상태: 정상")
            valid_label.setStyleSheet("color: #2e7d32;")
            valid_label.setToolTip("")
        else:
            reasons = "\n".join(
                "%s: %s" % (key, reason) for key, reason in form.invalid_items()
            )
            valid_label.setText("입력 상태: 확인 필요")
            valid_label.setStyleSheet("color: #e53935;")
            valid_label.setToolTip(reasons)

    def on_run():
        append("[폼] --- 값 ---")
        for key, value in form.values().items():
            append("[폼]   %s: %r" % (key, value))
        append("[폼]   유효: %s" % form.is_valid())
        for key, reason in form.invalid_items():
            append("[폼]   ! %s: %s" % (key, reason))

    run_btn.clicked.connect(on_run)
    clear_btn.clicked.connect(form.clear)
    form.validityChanged.connect(lambda _ok: refresh_form_state())
    form.valueChanged.connect(lambda key, path: append("[폼] %s = %s" % (key, path)))
    refresh_form_state()

    return section, form


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("jekai")
    app.setApplicationName("filedialog-widget-demo")

    # 최근 경로/마지막 폴더를 QSettings 에 저장할 때 쓸 기본 정보.
    # (settings_key 를 준 위젯들이 이 정보를 공유한다)
    configure_settings("jekai", "filedialog-widget-demo")

    window = QMainWindow()
    window.setWindowTitle("FilePathEdit 데모")
    window.resize(1180, 900)

    central = QWidget()
    root = QVBoxLayout(central)
    root.setSpacing(8)

    # 로그를 먼저 만들어 두어야 각 빌더가 append 를 넘겨받을 수 있다.
    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setMaximumBlockCount(300)

    def append(text):
        log.appendPlainText(text)

    # 공통 옵션을 한꺼번에 적용하기 위해 만들어진 위젯을 모아 둔다.
    edits = []

    def track(edit):
        edits.append(edit)
        return edit

    # ---------------------------------------------------------- 공통 옵션
    option_row = QHBoxLayout()
    option_row.addWidget(QLabel("공통 옵션:"))

    native_check = QCheckBox("네이티브 다이얼로그")
    native_check.setChecked(True)
    native_check.setToolTip(
        "끄면 Qt 자체 다이얼로그로 열립니다.\n"
        "사이드바를 지정한 위젯은 이 설정과 무관하게 항상 Qt 자체 창입니다."
    )
    option_row.addWidget(native_check)

    dnd_check = QCheckBox("드래그&드롭")
    dnd_check.setChecked(True)
    option_row.addWidget(dnd_check)
    option_row.addStretch(1)
    root.addLayout(option_row)

    # ------------------------------------------------------------ 두 단으로
    form_section, _form = build_form_section(append)

    # 세로 길이가 얼추 맞도록 나눠 담는다(왼쪽이 짧은 것 위주).
    columns = [
        [
            build_modes_section(track, append),
            build_filters_section(track, append),
            build_validation_section(track, append),
            build_history_section(track, append),
            form_section,
        ],
        [
            build_sidebar_section(track, append),
            build_places_section(track, append),
            build_safety_section(track, append),
        ],
    ]

    board = QWidget()
    board_layout = QHBoxLayout(board)
    board_layout.setContentsMargins(0, 0, 0, 0)
    for sections in columns:
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        for section in sections:
            column_layout.addWidget(section)
        column_layout.addStretch(1)      # 남는 세로는 아래쪽에 몰아 준다
        board_layout.addWidget(column, 1)

    # 한 화면에 다 안 들어가므로 통째로 스크롤한다
    scroll = QScrollArea()
    scroll.setWidget(board)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)

    # 로그 높이는 사용자가 끌어서 조절한다
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.addWidget(scroll)
    splitter.addWidget(log)
    splitter.setStretchFactor(0, 4)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([700, 180])
    root.addWidget(splitter, 1)

    window.setCentralWidget(central)

    def on_native(checked):
        for edit in edits:
            edit.set_native(checked)
        append("[공통] 네이티브 다이얼로그: %s" % checked)

    def on_dnd(checked):
        for edit in edits:
            edit.set_drag_drop_enabled(checked)
        append("[공통] 드래그&드롭: %s" % checked)

    native_check.toggled.connect(on_native)
    dnd_check.toggled.connect(on_dnd)

    append(
        "모든 기능을 한 화면에 폈습니다. 스크롤해서 확인해 보세요. (위젯 %d개)"
        % len(edits)
    )

    window.show()
    sys.exit(app.exec_() if hasattr(app, "exec_") else app.exec())


if __name__ == "__main__":
    main()
