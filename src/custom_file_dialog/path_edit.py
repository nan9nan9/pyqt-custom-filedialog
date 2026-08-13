"""FilePathEdit: 입력창 + 찾아보기 버튼으로 이루어진 경로 선택 위젯."""

import os

from qtpy.QtCore import QDir, Qt, Signal
from qtpy.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from . import dialog as dialog_module
from . import drops
from .constants import (
    DEFAULT_BUTTON_TEXT,
    DEFAULT_CAPTIONS,
    DEFAULT_HISTORY_SIZE,
    DEFAULT_MUST_EXIST,
    DEFAULT_PLACEHOLDERS,
    DEFAULT_SEPARATOR,
    INVALID_BORDER_COLOR,
    SelectMode,
    is_multi_mode,
    normalize_mode,
)
from . import safety
from .hooks import GuardedFileSystemModel
from .icons import standard_icon
from .places import PlacesOptions
from .filters import build_filter, ensure_suffix, suffix_of
from .history import PathHistory
from .validators import isdir_check, join_paths, split_paths, validate_paths


class FilePathEdit(QWidget):
    """경로 하나(또는 여럿)를 고르는 한 줄짜리 위젯.

    ``[라벨] [입력창] [▾] [...]`` 구조이며, ``...`` 버튼을 누르면 모드에 맞는
    :class:`QFileDialog` 가 열리고 선택 결과가 입력창에 채워진다.

    사용 예::

        edit = FilePathEdit(
            mode="open_file",
            label="입력 파일:",
            filters=[("CSV", ["csv"]), ("모든 파일", ["*"])],
        )
        edit.pathChanged.connect(print)
        path = edit.path()

    시그널:
        pathChanged(str): 경로 텍스트가 바뀔 때마다(직접 입력 포함).
        pathsChanged(list): 위와 같으나 경로 리스트로 전달.
        validityChanged(bool): 유효/무효 상태가 바뀔 때.
        browsed(list): 다이얼로그에서 실제로 선택을 마쳤을 때.
        editingFinished(): 입력창 편집이 끝났을 때(포커스 아웃/Enter).
    """

    pathChanged = Signal(str)
    pathsChanged = Signal(list)
    validityChanged = Signal(bool)
    browsed = Signal(list)
    editingFinished = Signal()

    def __init__(
        self,
        mode=SelectMode.OPEN_FILE,
        label=None,
        caption=None,
        filters=None,
        add_all_files_filter=True,
        default_suffix=None,
        start_dir=None,
        placeholder=None,
        button_text=None,
        button_icon=False,
        must_exist=None,
        required=False,
        validate=True,
        drag_drop=True,
        completer=True,
        history=0,
        settings_key=None,
        settings=None,
        native=True,
        path_timeout=safety.DEFAULT_TIMEOUT,
        sidebar_urls=None,
        fixed_sidebar_urls=None,
        favorites=None,
        favorites_icon=True,
        recent_files=False,
        recent_max=None,
        read_only=False,
        clear_button=True,
        separator=DEFAULT_SEPARATOR,
        parent=None,
    ):
        """
        Args:
            mode: ``"open_file"`` / ``"open_files"`` / ``"save_file"`` / ``"directory"``.
            label: 입력창 왼쪽에 붙일 라벨. None 이면 라벨 없음.
            caption: 다이얼로그 제목. None 이면 모드별 기본 제목.
            filters: ``[("이미지", ["png","jpg"])]`` 또는 Qt 필터 문자열.
            add_all_files_filter: 필터 끝에 "모든 파일 (*)" 을 자동으로 붙인다.
            default_suffix: 저장 모드에서 확장자가 없을 때 붙일 확장자.
            start_dir: 다이얼로그가 처음 열릴 폴더.
            placeholder: 입력창 안내 문구. None 이면 모드별 기본값.
            button_text: 찾아보기 버튼 텍스트 (기본 ``"..."``).
            button_icon: True 면 텍스트 대신 폴더 아이콘을 쓴다.
            must_exist: 경로가 존재해야 유효한지. None 이면 모드별 기본값.
            required: 비어 있는 것도 오류로 볼지 여부.
            validate: 유효성 표시(테두리/툴팁) 사용 여부.
            drag_drop: 파일을 끌어다 놓아 채우는 기능.
            completer: 경로 입력 시 파일시스템 자동완성. 완성 후보를 만들려면
                그 폴더를 통째로 읽어야 하므로, 파일이 수만 개이거나 automount 가
                잔뜩 달린 자리를 다루는 앱이라면 ``False`` 로 꺼 두는 편이 낫다
                (:meth:`set_completer` 로 실행 중에도 바꿀 수 있다).
            history: 0 보다 크면 최근 경로 드롭다운(▾)을 만든다.
            settings_key: QSettings 에 최근 경로/마지막 폴더를 저장할 키.
            settings: 직접 만든 QSettings 인스턴스(없으면 기본값 사용).
            native: OS 네이티브 다이얼로그 사용 여부.
            path_timeout: 죽은 네트워크 경로(NFS 등)에서 멈추지 않도록 하는 안전
                확인의 제한 시간(초). 기본값은 켜져 있다
                (:data:`~custom_file_dialog.safety.DEFAULT_TIMEOUT`).
                유효성 검사와 다이얼로그 시작 폴더 결정에 적용되며, **로컬 경로는
                평소와 똑같이** 처리하므로 부담이 없다. ``None`` 을 주면 끈다.
                :mod:`~custom_file_dialog.safety` 참고.
            sidebar_urls: 다이얼로그 왼쪽 사이드바에 표시할 위치 목록
                (경로 문자열 또는 QUrl). 지정하면 네이티브 다이얼로그를 쓸 수
                없어 Qt 자체 다이얼로그로 자동 전환된다.
            fixed_sidebar_urls: 사이드바 우클릭 "사이드바에서 제거"를 막을 위치
                목록. None 이면 **사용자 홈만** 보호한다. ``[]`` 를 주면 아무것도
                보호하지 않고, 경로를 나열하면 그 위치들도 함께 보호한다.
            favorites: :class:`~custom_file_dialog.favorites.FavoritesStore`,
                또는 ``True`` 면 **기본 위치**에 하나 만들어 쓴다(다이얼로그와
                같은 규칙). 분류들이 사이드바에 덧붙고, 고른 경로는 자동으로
                원본 경로로 복원된다. ``sidebar_urls`` 를 따로 주지 않았으면
                기존 사이드바 항목 뒤에 분류가 붙는다.
            favorites_icon: 즐겨찾기 분류를 폴더 아이콘 대신 **별표**로 표시한다
                (기본 True). ``QIcon`` 을 주면 그 아이콘을 쓰고, False 면 Qt 기본
                폴더 아이콘을 그대로 둔다.
            recent_files: 최근에 고른 파일을 모아 두는 "최근 파일" 항목을
                사이드바에 추가한다 (기본 False = 안 씀). ``True`` 면 기본 위치에
                :class:`~custom_file_dialog.recent.RecentStore` 를 하나 만들어 쓰고,
                직접 만든 저장소를 넘겨 여러 위젯이 공유하게 할 수도 있다.
            recent_max: ``recent_files=True`` 로 저장소를 자동 생성할 때 기억할
                개수(기본 20). 저장소를 직접 넘겼다면 무시된다.
            read_only: 입력창 직접 편집 금지(버튼/드롭으로만 지정).
            clear_button: 입력창 안 X 버튼 표시 여부.
            separator: open_files 모드에서 여러 경로를 잇는 구분자.
        """
        super().__init__(parent)

        self._mode = normalize_mode(mode)
        self._caption = caption
        self._filters = filters
        self._add_all_files_filter = add_all_files_filter
        self._name_filter = build_filter(filters, add_all_files=add_all_files_filter)
        self._selected_filter = None
        self._default_suffix = default_suffix
        self._start_dir = start_dir
        self._must_exist = (
            DEFAULT_MUST_EXIST[self._mode] if must_exist is None else bool(must_exist)
        )
        self._required = bool(required)
        self._validate_enabled = bool(validate)
        self._drag_drop = bool(drag_drop)
        self._native = bool(native)
        self._path_timeout = None if path_timeout is None else float(path_timeout)
        # 사이드바에 얹을 것들의 설정은 한 객체가 들고 있다가 Places 를 만든다
        # (같은 관리를 다이얼로그도 하므로 places 모듈에 모아 두었다).
        self._options = PlacesOptions(
            favorites=favorites,
            recent=recent_files,
            recent_max=recent_max,
            sidebar_urls=sidebar_urls,
            fixed_urls=fixed_sidebar_urls,
            icon=favorites_icon,
        )
        self._separator = separator or DEFAULT_SEPARATOR
        self._valid = True
        self._reason = ""
        self._base_tooltip = ""

        history_size = DEFAULT_HISTORY_SIZE if history is True else int(history or 0)
        if settings_key and history_size <= 0:
            # 키만 주고 개수를 안 준 경우에도 기억은 하도록 한다.
            history_size = DEFAULT_HISTORY_SIZE
        self._history = PathHistory(
            key=settings_key, max_items=history_size, settings=settings
        )

        self._build_ui(
            label=label,
            placeholder=placeholder,
            button_text=button_text,
            button_icon=button_icon,
            read_only=read_only,
            clear_button=clear_button,
            history_size=history_size,
        )

        # 자동완성: 실제 파일시스템을 대상으로 경로를 완성해 준다.
        self._completer = None
        self._completer_model = None
        if completer:
            self._install_completer()

        # 드롭은 이 위젯이 직접 처리한다. QLineEdit 이 드롭을 먼저 먹어
        # URL 문자열이 그대로 붙는 것을 막기 위해 자식은 드롭을 받지 않는다.
        # 끈 경우에도 QLineEdit 의 기본 드롭을 꺼야 한다. 그대로 두면 파일을
        # 떨어뜨렸을 때 "file:///…" 원문이 입력창에 붙는다(우리가 막으려던 것).
        self.setAcceptDrops(self._drag_drop)
        self._edit.setAcceptDrops(False)


        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.editingFinished.connect(self.editingFinished)

        self._update_validity()

    # ------------------------------------------------------------ UI 조립
    def _build_ui(self, label, placeholder, button_text, button_icon,
                  read_only, clear_button, history_size):
        """``[라벨] [입력창] [▾] [...]`` 한 줄을 만든다.

        생성자에서 떼어 둔다 — 설정을 받아 상태를 세우는 일과 위젯을
        늘어놓는 일은 함께 읽을 이유가 없다.
        """
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._label = None
        if label:
            self._label = QLabel(label, self)
            layout.addWidget(self._label)

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(
            placeholder if placeholder is not None else DEFAULT_PLACEHOLDERS[self._mode]
        )
        self._clear_button = bool(clear_button)
        self._edit.setReadOnly(bool(read_only))
        self._edit.setClearButtonEnabled(self._clear_button and not read_only)
        self._edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._edit, 1)

        self._history_button = None
        if history_size > 0:
            self._history_button = QToolButton(self)
            self._history_button.setText("▾")
            self._history_button.setToolTip("최근에 선택한 경로")
            self._history_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self._history_menu = QMenu(self._history_button)
            self._history_button.setMenu(self._history_menu)
            self._history_menu.aboutToShow.connect(self._rebuild_history_menu)
            layout.addWidget(self._history_button)

        self._button = QToolButton(self)
        if button_icon:
            icon = standard_icon(self, "SP_DirOpenIcon")
            if icon is not None:
                self._button.setIcon(icon)
            else:
                self._button.setText(button_text or DEFAULT_BUTTON_TEXT)
        else:
            self._button.setText(button_text or DEFAULT_BUTTON_TEXT)
        self._button.setToolTip(self._caption or DEFAULT_CAPTIONS[self._mode])
        self._button.clicked.connect(self.browse)
        layout.addWidget(self._button)

    # ------------------------------------------------------------ 내부 구성
    def _install_completer(self):
        model = GuardedFileSystemModel(self)
        model.setRootPath("")
        if self._mode == SelectMode.DIRECTORY:
            model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives)
        self._completer_model = model
        self._completer = QCompleter(model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._edit.setCompleter(self._completer)

    def set_completer(self, enabled):
        """경로 자동완성을 켜고 끈다.

        완성 후보를 만들려면 Qt 가 그 폴더를 **통째로 읽어야** 한다. 파일이 수만
        개이거나 automount 가 잔뜩 달린 자리에서는 그것만으로 오래 걸리므로,
        그런 곳을 다루는 화면에서는 꺼 두는 편이 낫다. 끄면 모델까지 버려서
        파일시스템 감시도 함께 멈춘다.

        경로를 직접 치거나 붙여 넣는 것, 유효성 표시, 다이얼로그는 그대로다.
        앱 전체에서 한꺼번에 끄려면
        :func:`safety.configure(allow_listing=False)
        <custom_file_dialog.safety.configure>` 쪽을 쓴다(다이얼로그의 파일 이름
        칸 자동완성까지 함께 막힌다).
        """
        if enabled == self.completer_enabled():
            return
        if enabled:
            self._install_completer()
            return

        self._edit.setCompleter(None)
        self._completer.deleteLater()
        self._completer_model.deleteLater()
        self._completer = self._completer_model = None

    def completer_enabled(self):
        """자동완성이 켜져 있는지."""
        return self._completer is not None

    def _rebuild_history_menu(self):
        self._history_menu.clear()
        items = self._history.items()
        if not items:
            action = self._history_menu.addAction("(최근 경로 없음)")
            action.setEnabled(False)
            return
        for path in items:
            action = self._history_menu.addAction(path)
            # 기본 인자로 값을 묶어 두지 않으면 마지막 path 만 잡힌다.
            action.triggered.connect(lambda checked=False, p=path: self.set_path(p))
        self._history_menu.addSeparator()
        self._history_menu.addAction("목록 지우기", self._history.clear)

    # ------------------------------------------------------------ 값 접근
    def path(self):
        """현재 경로(첫 번째). 비어 있으면 빈 문자열."""
        paths = self.paths()
        return paths[0] if paths else ""

    def paths(self):
        """현재 경로 리스트. open_files 모드가 아니면 0~1개.

        **여러 개 모드일 때만** 구분자로 쪼갠다. 리눅스·맥에서는 ``a;b.txt`` 도
        합법인 파일 이름이라, 단일 선택인데 쪼개면 멀쩡한 경로가 두 동강 난다.
        """
        text = self._edit.text()
        if is_multi_mode(self._mode):
            return split_paths(text, self._separator)
        text = (text or "").strip()
        return [text] if text else []

    def set_path(self, path):
        """경로 하나를 지정한다."""
        self.set_paths([path] if path else [])

    def set_paths(self, paths):
        """경로 리스트를 지정한다(open_files 모드가 아니면 첫 항목만)."""
        paths = [str(p) for p in (paths or []) if p]
        if not is_multi_mode(self._mode):
            paths = paths[:1]
        self._edit.setText(join_paths(paths, self._separator))

    def text(self):
        """입력창의 원본 텍스트."""
        return self._edit.text()

    def clear(self):
        self._edit.clear()

    def is_valid(self):
        """현재 경로가 유효한지 여부."""
        return self._valid

    def invalid_reason(self):
        """유효하지 않은 이유(유효하면 빈 문자열)."""
        return self._reason

    # ---------------------------------------------------------- 다이얼로그
    def browse(self):
        """다이얼로그를 열어 경로를 고른다. 선택한 경로 리스트를 반환한다.

        코드에서 직접 호출해도 되고(버튼 없이 쓰는 경우), 취소하면 빈
        리스트를 반환하며 현재 값은 그대로 둔다.
        """
        paths, chosen = dialog_module.exec_file_dialog(
            parent=self.window(),
            mode=self._mode,
            caption=self._caption,
            directory=self._start_dir_now(),
            filters=self._name_filter,
            selected_filter=self._selected_filter,
            native=self._native,
            default_suffix=self._default_suffix,
            places=self._places(),
            path_timeout=self._path_timeout,    # 위젯 설정을 다이얼로그까지
        )
        self._selected_filter = chosen or self._selected_filter

        if not paths:
            return []

        # exec_file_dialog 도 같은 손질을 하지만, 그 함수는 **갈아 끼우라고 열어 둔
        # 자리**다(테스트에서 monkeypatch 한다). 위젯이 내놓는 path() 가 늘 원본
        # 경로에 확장자까지 붙은 값이도록 여기서 한 번 더 보장한다. 둘 다 여러 번
        # 적용해도 결과가 같은 연산이라 겹쳐도 안전하다.
        paths = self._places().resolve_all(paths)
        if self._mode == SelectMode.SAVE_FILE:
            suffix = self._default_suffix or suffix_of(self._selected_filter)
            paths = [ensure_suffix(paths[0], suffix)]

        self.set_paths(paths)
        self._remember(paths)
        # 고른 순서대로 기록해 마지막 것이 가장 최근이 되게 한다
        self._places().record_recent(paths)
        self.browsed.emit(list(paths))
        return list(paths)

    def _places(self):
        """사이드바에 얹을 것들의 묶음(설정이 바뀌면 다시 만든다)."""
        return self._options.places()

    def _isdir(self, path):
        """죽은 마운트에서 멈추지 않는 isdir (위젯의 path_timeout 설정을 따른다)."""
        return isdir_check(self._path_timeout)(path)

    def _remember(self, paths):
        first = paths[0]
        self._history.add(first)
        directory = first if self._isdir(first) else os.path.dirname(first)
        self._history.set_last_dir(directory)

    def history_items(self):
        """최근 경로 목록(최신순)."""
        return self._history.items()

    # ------------------------------------------------------------- 유효성
    def _on_text_changed(self, text):
        self._update_validity()
        self.pathChanged.emit(self.path())
        self.pathsChanged.emit(self.paths())

    def _update_validity(self):
        if not self._validate_enabled:
            valid, reason = True, ""
        else:
            valid, reason = validate_paths(
                self.paths(),
                mode=self._mode,
                must_exist=self._must_exist,
                required=self._required,
                timeout=self._path_timeout,
            )

        changed = valid != self._valid
        self._valid = valid
        self._reason = reason
        self._apply_validity_style()
        if changed:
            self.validityChanged.emit(valid)

    def _apply_validity_style(self):
        if self._valid:
            self._edit.setStyleSheet("")
            self._edit.setToolTip(self._base_tooltip)
        else:
            self._edit.setStyleSheet(
                "QLineEdit { border: 1px solid %s; }" % INVALID_BORDER_COLOR
            )
            tip = self._reason
            if self._base_tooltip:
                tip = "%s\n%s" % (self._base_tooltip, tip)
            self._edit.setToolTip(tip)

    # --------------------------------------------------------------- 설정
    def set_mode(self, mode):
        """선택 모드를 바꾼다(placeholder / must_exist 기본값도 함께 갱신)."""
        mode = normalize_mode(mode)
        if mode == self._mode:
            return
        old_default = DEFAULT_MUST_EXIST[self._mode]
        # paths() 는 모드에 따라 쪼개는 방식이 다르다. 새 모드를 넣기 전에
        # **지금 모드 기준으로** 읽어 두어야 여러 개 -> 하나 전환에서 첫 경로만
        # 남길 수 있다(나중에 읽으면 합쳐진 텍스트가 통째로 한 경로가 된다).
        current = self.paths()
        self._mode = mode
        # must_exist 를 따로 지정하지 않았던 경우에만 새 모드 기본값을 따른다.
        if self._must_exist == old_default:
            self._must_exist = DEFAULT_MUST_EXIST[mode]
        self._edit.setPlaceholderText(DEFAULT_PLACEHOLDERS[mode])
        self._button.setToolTip(self._caption or DEFAULT_CAPTIONS[mode])
        # 자동완성 후보도 모드를 따라간다(폴더 모드는 폴더만, 나머지는 전부)
        if self._completer_model is not None:
            if mode == SelectMode.DIRECTORY:
                self._completer_model.setFilter(
                    QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Drives
                )
            else:
                self._completer_model.setFilter(
                    QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot
                )
        if not is_multi_mode(mode):
            self.set_paths(current[:1])
        self._update_validity()

    def mode(self):
        return self._mode

    def set_filters(self, filters, add_all_files_filter=None):
        """필터를 바꾼다. 형식은 생성자의 ``filters`` 와 같다."""
        if add_all_files_filter is not None:
            self._add_all_files_filter = bool(add_all_files_filter)
        self._filters = filters
        self._name_filter = build_filter(
            filters, add_all_files=self._add_all_files_filter
        )
        self._selected_filter = None

    def name_filter(self):
        """실제로 QFileDialog 에 넘기는 Qt 필터 문자열."""
        return self._name_filter

    def set_start_dir(self, directory):
        self._start_dir = directory

    def set_caption(self, caption):
        self._caption = caption
        self._button.setToolTip(caption or DEFAULT_CAPTIONS[self._mode])

    def set_required(self, required):
        self._required = bool(required)
        self._update_validity()

    def required(self):
        return self._required

    def set_must_exist(self, must_exist):
        self._must_exist = bool(must_exist)
        self._update_validity()

    def must_exist(self):
        """경로가 존재해야 유효한지 여부(모드를 바꾸면 기본값이 따라간다)."""
        return self._must_exist

    def set_validate_enabled(self, enabled):
        self._validate_enabled = bool(enabled)
        self._update_validity()

    def set_path_timeout(self, timeout):
        """네트워크 경로 안전 확인에 쓸 제한 시간(초). None 이면 끈다."""
        self._path_timeout = None if timeout is None else float(timeout)
        self._update_validity()

    def path_timeout(self):
        return self._path_timeout

    def set_native(self, native):
        self._native = bool(native)

    def set_sidebar_urls(self, urls):
        """다이얼로그 왼쪽 사이드바 항목을 지정한다.

        경로 문자열과 ``QUrl`` 을 섞어 줄 수 있고, ``~`` 는 홈으로 펼쳐진다::

            edit.set_sidebar_urls(["~", "~/프로젝트", "/mnt/data"])

        기존 항목(Computer, 홈 등)을 남기고 뒤에 덧붙이려면::

            from custom_file_dialog import current_sidebar_urls, to_urls
            edit.set_sidebar_urls(current_sidebar_urls() + to_urls(["/mnt/data"]))

        ``[]`` 를 주면 사이드바가 비고, ``None`` 이면 커스터마이즈를 끈다
        (= 저장된 사이드바 + 네이티브 다이얼로그 사용 가능 상태로 복귀).

        주의:
        - 사이드바를 지정하면 네이티브 다이얼로그를 쓸 수 없어, ``native``
          설정과 무관하게 Qt 자체 다이얼로그로 열린다.
        - Qt 가 사이드바 항목을 사용자 설정에 **영구 저장**하므로, 한 번 지정하면
          다음 실행에도 남는다(``None`` 으로 되돌려도 저장된 항목이 보인다).
        """
        self._options.update(sidebar_urls=urls)

    def sidebar_urls(self):
        """지정된 사이드바 항목 목록(커스터마이즈하지 않았으면 None).

        즐겨찾기로 덧붙는 분류는 포함되지 않는다. 다이얼로그에 실제로 넘어가는
        최종 목록이 필요하면 :meth:`effective_sidebar_urls` 를 쓴다.
        """
        urls = self._options.sidebar_urls
        return list(urls) if urls is not None else None

    def _current_dir_now(self):
        """지금 열면 다이얼로그가 실제로 **서 있을 폴더**.

        저장 모드에서는 시작 위치로 파일 경로가 나온다(다이얼로그가 그 이름을
        미리 채우도록). 사이드바의 "현재 위치"는 폴더라야 하므로 그 경우
        상위 폴더로 바꾼다 — 다이얼로그가 실제로 하는 것과 같다.
        """
        start = self._start_dir_now()
        if start and not self._isdir(start):
            return os.path.dirname(start) or start
        return start

    def effective_sidebar_urls(self):
        """지금 열면 사이드바에 실제로 들어갈 목록(홈 · 현재 위치 · 최근 · 북마크).

        "현재 위치"는 열리는 시점에 정해지므로, 지금 열었을 때의 시작 폴더를
        기준으로 계산한다.
        """
        urls = self._places().sidebar_urls(self._current_dir_now())
        return list(urls) if urls is not None else None

    def effective_sidebar_marks(self):
        """지금 열면 사이드바에서 표시가 바뀔 항목 — ``{경로: (이름, 아이콘)}``.

        홈은 집 아이콘, 다이얼로그가 열리는 자리는 "현재 위치"라는 이름으로
        나온다. :meth:`effective_sidebar_urls` 와 같은 시작 폴더를 기준으로
        계산하므로 두 결과는 늘 짝이 맞는다
        (:meth:`~custom_file_dialog.places.Places.sidebar_marks` 참고).
        """
        return self._places().sidebar_marks(self._current_dir_now())

    def _start_dir_now(self):
        """지금 browse() 하면 다이얼로그가 열릴 폴더."""
        return dialog_module.resolve_start_dir(
            self.paths(),
            start_dir=self._start_dir,
            last_dir=self._history.last_dir(),
            mode=self._mode,
            timeout=self._path_timeout,
        )

    def set_fixed_sidebar_urls(self, urls):
        """사이드바 우클릭 "제거"를 막을 위치 목록.

        ``None`` 이면 사용자 홈만 보호하고, ``[]`` 면 아무것도 보호하지 않는다.
        """
        self._options.update(fixed_urls=urls)

    def fixed_sidebar_urls(self):
        """지정한 보호 위치 목록(지정 안 했으면 None = 홈만 보호)."""
        urls = self._options.fixed_urls
        return list(urls) if urls is not None else None

    def set_favorites(self, store):
        """즐겨찾기 저장소를 지정한다(``None`` 이면 사용 안 함).

        분류들이 사이드바 뒤에 덧붙고, 거기서 고른 경로는 자동으로 원본
        경로로 복원된다. 즐겨찾기를 쓰면 사이드바를 건드리게 되므로
        네이티브 다이얼로그 대신 Qt 자체 다이얼로그로 열린다.
        """
        self._options.update(favorites=store)

    def favorites(self):
        """지정된 :class:`FavoritesStore` (없으면 None)."""
        return self._options.favorites

    def set_favorites_icon(self, icon):
        """즐겨찾기 분류에 씌울 아이콘.

        ``True`` 면 기본 별표, ``QIcon`` 이면 그 아이콘, ``False`` 면 Qt 기본
        폴더 아이콘을 그대로 쓴다.
        """
        self._options.update(icon=icon)

    def set_recent_files(self, recent_files, recent_max=None):
        """"최근 파일" 사이드바 항목을 켜거나 끈다.

        ``True`` 면 기본 위치에 저장소를 만들어 쓰고, ``False``/``None`` 이면
        쓰지 않는다. :class:`~custom_file_dialog.recent.RecentStore` 를 직접
        넘기면 여러 위젯이 같은 목록을 공유할 수 있다.
        """
        self._options.update(recent=recent_files, recent_max=recent_max)

    def recent_files(self):
        """쓰고 있는 :class:`RecentStore` (안 쓰면 None)."""
        return self._options.recent

    def recent_items(self):
        """최근에 고른 파일 목록(최신순). 최근 파일을 안 쓰면 빈 리스트."""
        recent = self._options.recent
        return recent.items() if recent is not None else []

    def set_read_only(self, read_only):
        self._edit.setReadOnly(bool(read_only))
        # 생성 시 끈 X 버튼이 read_only 토글로 되살아나지 않게 한다
        self._edit.setClearButtonEnabled(self._clear_button and not read_only)

    def set_drag_drop_enabled(self, enabled):
        """끌어다 놓기를 켜고 끈다.

        꺼도 입력창이 Qt 기본 드롭을 받지 않게 한다 — 받으면 URL 원문이 그대로
        붙어 경로가 아닌 값이 들어간다.
        """
        self._drag_drop = bool(enabled)
        self.setAcceptDrops(self._drag_drop)
        self._edit.setAcceptDrops(False)

    def set_label(self, text):
        """라벨 텍스트를 바꾼다(생성 시 label 을 준 경우에만 유효)."""
        if self._label is not None:
            self._label.setText(text)

    def set_tooltip(self, text):
        """입력창 기본 툴팁. 무효 상태에서는 사유가 아래에 덧붙는다."""
        self._base_tooltip = text or ""
        self._apply_validity_style()

    # -------------------------------------------------------- 내부 위젯 접근
    @property
    def line_edit(self):
        """내부 QLineEdit (스타일 커스터마이즈 등에 사용)."""
        return self._edit

    @property
    def browse_button(self):
        """내부 찾아보기 QToolButton."""
        return self._button

    @property
    def label_widget(self):
        """내부 QLabel (label 을 주지 않았으면 None)."""
        return self._label

    # ------------------------------------------------------------ 드래그&드롭
    def dragEnterEvent(self, event):  # noqa: N802 (Qt 시그니처)
        if self._drag_drop and self._acceptable_paths(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802 (Qt 시그니처)
        if self._drag_drop and self._acceptable_paths(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):  # noqa: N802 (Qt 시그니처)
        paths = self._acceptable_paths(event)
        if not self._drag_drop or not paths:
            event.ignore()
            return
        self.set_paths(paths)
        if paths:
            self._remember(paths)
        event.acceptProposedAction()

    def _acceptable_paths(self, event):
        """드래그된 항목 중 현재 모드에 맞는 로컬 경로만 골라 반환한다.

        규칙 자체는 Qt 와 무관하므로 :mod:`~custom_file_dialog.drops` 에 있다.
        여기서는 이벤트에서 URL 을 꺼내 넘기기만 한다.
        """
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return drops.acceptable_paths(mime.urls(), self._mode, self._isdir)
