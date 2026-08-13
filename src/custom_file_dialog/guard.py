"""나열하면 안 되는 자리를 다이얼로그가 건드리지 못하게 막는다.

``/user`` 처럼 아래에 automount 가 잔뜩 달린 자리는 **목록을 읽는 것만으로**
전부 마운트되면서 시스템이 주저앉는다. 목록을 읽게 만드는 통로를 모두 막는다.

- 자동완성 모델을 :class:`GuardedFileSystemModel` 로 갈아 끼운다
- 파일 이름 칸의 **키 입력마다 도는 자동 경로 확인**을 위험한 자리에서 끈다
  (:class:`_TypingGuard` — ``/user/my`` 를 치는 순간의 stat 이 곧 마운트 시도다)
- 파일 목록에서 더블클릭/Enter 로 여는 것을 막는다
- "Look in" 드롭다운에서 고르는 것을 막는다
- 파일 이름 칸에 치고 확정하는 것을 막는다

무엇을 막을지는 :mod:`~custom_file_dialog.safety` 의 전역 설정
(``guarded_roots`` · ``min_depth`` · ``allow_listing``)이 정하고, autofs 마운트
지점은 설정이 없어도 마운트 표에서 자동으로 알아본다
(:func:`~custom_file_dialog.safety.may_stat`).

Qt 가 C++ 에서 연결해 둔 ``activated`` · ``accept()`` 는 파이썬에서 끊을 수 없다.
그래서 **그 신호가 나기 전 단계인 입력 이벤트를 삼키는** 방식으로 막는다.
(파일 이름 칸의 ``textChanged`` 는 예외적으로 끊을 수 있다 — 받는 쪽이 아니라
**보내는 쪽**에서 전부 끊고, 안전할 때만 같은 슬롯을 이름으로 다시 부른다.)
"""

import os

from qtpy.QtCore import QEvent, QMetaObject, QObject, Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileSystemModel,
    QLineEdit,
    QListView,
    QMessageBox,
    QTreeView,
)

try:                                # PySide2 에는 없다 (있으면 invoke 경로에 쓴다)
    from qtpy.QtCore import Q_ARG
except ImportError:
    Q_ARG = None

try:                                # PyQt6 에는 없다 (있으면 relay 경로에 쓴다)
    from qtpy.QtCore import SIGNAL, SLOT
except ImportError:
    SIGNAL = SLOT = None

from . import safety
from .constants import PATH_ROLE
from .qt_compat import enum_value
from .util import abspath, url_path

_OPEN_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class GuardedFileSystemModel(QFileSystemModel):
    """위험한 자리의 목록을 **아예 요청하지 않는** 파일시스템 모델.

    자식이 없다고 답해서 Qt 가 그 폴더를 읽지 않게 한다. 세 가지를 막는다
    (:func:`~custom_file_dialog.safety.may_list` 가 셋을 한 번에 판정한다).

    - ``guarded_roots`` 로 지목한 자리 (``/user``). 하위 경로(``/user/myaccount``)는
      평소대로 동작한다.
    - ``min_depth`` 보다 얕은 자리. 위험한 자리를 미리 다 적어 두기 어려울 때,
      ``/user`` 같은 얕은 곳을 통째로 나열하지 않게 하는 그물이다.
    - ``allow_listing=False`` 면 깊이와 무관하게 **전부**.

    자동완성 모델로 쓰인다. 목록을 읽는 것만 막으므로 경로를 끝까지 직접 쳐서
    쓰는 것은 그대로 된다.
    """

    def _blocked(self, index):
        if not safety.listing_allowed():
            return True                      # 통째로 꺼 두었다
        if not index.isValid():
            return False                     # 모델 뿌리는 건드리지 않는다
        return not safety.may_list(self.filePath(index))

    def hasChildren(self, index):        # noqa: N802 (Qt 시그니처)
        return False if self._blocked(index) else super().hasChildren(index)

    def canFetchMore(self, index):       # noqa: N802 (Qt 시그니처)
        return False if self._blocked(index) else super().canFetchMore(index)

    def fetchMore(self, index):          # noqa: N802 (Qt 시그니처)
        if self._blocked(index):
            return
        super().fetchMore(index)


class _ItemBlocker(QObject):
    """뷰에서 차단 경로 항목을 **여는 이벤트**를 삼킨다.

    파일 목록은 더블클릭으로, 콤보 팝업은 클릭(release)으로 열리므로 어떤
    이벤트를 볼지는 ``open_events`` 로 정한다. Enter 키는 양쪽 공통이다.
    """

    def __init__(self, view, open_events, after=None, parent=None):
        super().__init__(parent if parent is not None else view)
        self._view = view
        self._events = open_events
        self._after = after              # 삼킨 뒤 할 일 (예: 팝업 닫기)
        self.blocked = []

    def eventFilter(self, obj, event):   # noqa: N802 (Qt 시그니처)
        try:
            return self._filter(event)
        except RuntimeError:
            # 다이얼로그가 닫히는 중이면 대상 위젯이 이미 사라졌을 수 있다
            return False

    def _filter(self, event):
        kind = event.type()
        if kind in self._events:
            index = self._view.indexAt(event.pos())
        elif kind == QEvent.Type.KeyPress and event.key() in _OPEN_KEYS:
            index = self._view.currentIndex()
        else:
            return False

        path = url_path(index.data(PATH_ROLE)) if index.isValid() else ""
        if path and safety.is_guarded(path):
            self.blocked.append(path)
            if self._after is not None:
                self._after()
            return True                  # 여는 동작 자체를 없던 일로
        return False


class _AcceptBlocker(QObject):
    """파일 이름 칸에 친 경로를 확정하는 것을 막는다 — 차단 경로와 얕은 경로.

    ``QDialog.accept()`` 는 Qt 가 C++ 에서 부르므로 가로챌 수 없다. 그 앞 단계인
    **Enter 키와 열기 버튼 클릭**을 삼킨다. accept 는 GUI 스레드에서 입력 경로를
    곧바로 stat 하므로, automount 아래의 얕은 경로(``/user/my``)는 확정 한 번으로
    멈춘다 — 무엇을 막을지는 :func:`~custom_file_dialog.safety.may_open`
    (``guarded_roots`` + 깊이 ≤ ``min_depth``)이 정한다.
    """

    def __init__(self, dialog, name_edit, parent=None):
        super().__init__(parent if parent is not None else dialog)
        self._dialog = dialog
        self._edit = name_edit
        self.blocked = []
        self.notice = None              # min_depth 안내 팝업 (재사용)

    def eventFilter(self, obj, event):   # noqa: N802 (Qt 시그니처)
        try:
            return self._filter(obj, event)
        except RuntimeError:
            return False

    def _filter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.KeyPress:
            keys = _OPEN_KEYS if obj is self._edit else _OPEN_KEYS + (Qt.Key.Key_Space,)
            if event.key() not in keys:
                return False
        elif kind == QEvent.Type.MouseButtonRelease:
            # 클릭으로 "확정"이 되는 건 열기/저장 버튼뿐이다. 입력창 클릭까지
            # 삼키면 차단 경로를 고치려고 칸을 클릭하는 것조차 안 된다.
            if obj is self._edit:
                return False
        else:
            return False

        # 상대 경로("..")도 현재 폴더 기준으로 풀되, 끝의 구분자는 "이 폴더를
        # 열겠다"는 명시적 표기라 판정(may_open)까지 살려서 넘긴다.
        raw = (self._edit.text() or "").strip()
        path = _typed_path(self._dialog, raw)
        if not path:
            return False
        opener = path
        if raw.endswith(("/", os.sep)) and not opener.endswith(os.sep):
            opener += os.sep
        if not safety.may_open(opener):
            self.blocked.append(path)
            self._explain(path)
            return True
        return False

    def _explain(self, path):
        """왜 안 열리는지 안내한다 — min_depth 규칙에 걸렸을 때만.

        지목해서 막은 자리(``guarded_roots``)는 기존처럼 조용히 삼킨다.
        모달 exec 는 쓰지 않는다 — Enter 를 연타해도 확인 버튼에 갇히지 않게
        비모달로 띄우고, 이미 떠 있으면 내용만 갱신한다.
        """
        if safety.is_guarded(path):
            return
        limit = safety.min_depth()
        if limit <= 0:
            return
        lines = [
            "이 깊이의 경로는 자동으로 열지 않습니다 (automount 보호).",
            "%d단계 이상의 경로를 입력하고, 폴더를 열려면 끝에 '%s' 를 붙이세요."
            % (limit, os.sep),
        ]
        example = path + os.sep
        if safety.may_open(example):
            lines.append("예) %s" % example)
        if self.notice is None:
            self.notice = QMessageBox(self._dialog)
            self.notice.setIcon(QMessageBox.Icon.Warning)
            self.notice.setWindowTitle("경로가 너무 얕습니다")
            self.notice.setStandardButtons(QMessageBox.StandardButton.Ok)
        self.notice.setText("\n".join(lines))
        self.notice.show()
        self.notice.raise_()


def _typed_path(dialog, text):
    """파일 이름 칸의 텍스트를 현재 폴더 기준의 절대 경로로 편다(비면 "")."""
    text = (text or "").strip()
    if not text:
        return ""
    if not os.path.isabs(text):
        text = os.path.join(dialog.directory().absolutePath(), text)
    return abspath(text)


def _split_typed(text):
    """파일 이름 칸의 텍스트를 경로 목록으로 나눈다(여러 개는 따옴표로 묶인다)."""
    if '"' not in text:
        return [text]
    parts = text.split('"')
    return [p for index, p in enumerate(parts) if index % 2 == 1 and p.strip()]


class _TypingGuard(QObject):
    """파일 이름 칸의 키 입력마다 도는 자동 경로 확인을 위험한 자리에서 끈다.

    Qt 는 글자를 칠 때마다(``textChanged``) 입력된 경로를 만져 본다 —
    ``_q_autoCompleteFileName`` 은 목록에서 맞는 항목을 고르려고,
    ``_q_updateOkButton`` 은 버튼 활성을 정하려고 ``access()``/``stat()`` 을
    부른다. automount 아래에서는 ``/user/my`` 같은 **미완성 경로를 만지는 것이
    곧 마운트 시도**라, 한 글자마다 시스템이 주저앉는다. ``guarded_roots`` ·
    ``min_depth`` 는 "나열"만 막으므로 이 통로는 잡지 못한다.

    ``textChanged`` 에 걸린 Qt 내부 동작을 **보내는 쪽에서** 전부 끊고, 입력된
    경로가 안전할 때만(:func:`~custom_file_dialog.safety.may_stat`) 같은 일을
    다시 해 준다. 위험한 자리에서 건너뛸 때도 **파일시스템을 만지지 않는 부분은
    반드시 대신 한다** — 목록 선택 동기화(:meth:`_sync_selection`)와 열기/저장
    버튼 활성이 그것이다. 그 둘을 빠뜨리면 확정이 엉뚱한 대상으로 가거나 아예
    안 된다. 확정(Enter·버튼) 자체는 원래 흐름대로 가되, 차단/얕은 경로는
    :class:`_AcceptBlocker` 가 계속 막는다.

    "같은 일을 다시" 하는 방법은 바인딩마다 다르다 (전부 설치 시점에 정한다):

    - **PyQt5**: ``QMetaObject.invokeMethod`` + ``Q_ARG`` 로 내부 슬롯을 이름으로
      재호출 (동작 동일).
    - **PySide2**: ``Q_ARG`` 가 없다. 대신 옛 방식
      ``QObject.connect(SIGNAL, SLOT)`` 이 되므로, 중계 시그널을 내부 슬롯에
      이어 두고 emit 한다 (동작 동일).
    - **Qt6 (PyQt6·PySide6)**: 내부 슬롯이 메타오브젝트에서 사라져 둘 다 안
      된다. 버튼 활성 판정을 :meth:`_update_accept_python` 으로 직접 하고,
      목록 항목 자동 하이라이트는 포기한다(표시만의 차이).
    """

    _SLOTS = ("_q_autoCompleteFileName(QString)", "_q_updateOkButton()")

    # PySide 옛 방식 connect 로 내부 슬롯에 이어 둘 중계 시그널
    relayText = Signal(str)
    relayPlain = Signal()

    def __init__(self, dialog, name_edit, parent=None):
        super().__init__(parent if parent is not None else dialog)
        self._dialog = dialog
        self._edit = name_edit
        self._route = None              # "invoke" | "relay" | "python"
        self.skipped = []               # 자동 확인을 건너뛴 경로들(진단용)

    @classmethod
    def install(cls, dialog, name_edit):
        """타이핑 가드를 건다. 걸 수 없는 환경이면 None (원래 연결을 그대로 둔다)."""
        if name_edit is None:
            return None
        guard = cls(dialog, name_edit)
        guard._pick_route()
        try:
            name_edit.textChanged.disconnect()
        except (TypeError, RuntimeError):
            guard.deleteLater()
            return None                 # 이미 끊겨 있는 등 — 손대지 않는다
        name_edit.textChanged.connect(guard.on_text_changed)
        return guard

    def _pick_route(self):
        """이 바인딩에서 되는 재호출 방법을 고른다 (클래스 docstring 참고)."""
        meta = self._dialog.metaObject()
        have_slots = all(meta.indexOfSlot(slot) >= 0 for slot in self._SLOTS)
        if have_slots and Q_ARG is not None:
            self._route = "invoke"
            return
        if have_slots and SIGNAL is not None and hasattr(QObject, "connect"):
            linked_text = QObject.connect(
                self, SIGNAL("relayText(QString)"),
                self._dialog, SLOT("_q_autoCompleteFileName(QString)"),
            )
            linked_plain = QObject.connect(
                self, SIGNAL("relayPlain()"),
                self._dialog, SLOT("_q_updateOkButton()"),
            )
            if linked_text and linked_plain:
                self._route = "relay"
                return
        self._route = "python"

    def on_text_changed(self, text):
        try:
            self._handle(text)
        except RuntimeError:
            pass                        # 다이얼로그가 닫히는 중

    def _handle(self, text):
        path = _typed_path(self._dialog, text)
        if path and not safety.may_stat(path):
            self.skipped.append(path)
            # Qt 가 하던 일 중 **파일시스템을 만지지 않는 부분**은 대신 해 준다.
            # 목록 선택 동기화를 빠뜨리면 이전 선택이 남아, 확정할 때
            # selectedFiles() 가 입력한 경로 대신 그 선택을 돌려준다(이동 불발).
            self._sync_selection(text)
            # 자동 판정 없이도 경로를 끝까지 쳐서 열 수는 있어야 한다.
            # (열기 시점의 존재 확인은 QFileDialog.accept() 가 한 번만 한다)
            self._enable_accept(bool(text.strip()))
            return
        if self._route == "invoke":
            QMetaObject.invokeMethod(
                self._dialog, "_q_autoCompleteFileName", Q_ARG(str, text)
            )
            QMetaObject.invokeMethod(self._dialog, "_q_updateOkButton")
        elif self._route == "relay":
            self.relayText.emit(text)
            self.relayPlain.emit()
        else:
            self._update_accept_python(text)

    def _sync_selection(self, text):
        """입력한 텍스트와 어긋나는 **묵은 목록 선택**을 지운다.

        ``QFileDialog.accept()`` 는 ``selectedFiles()`` 를 쓰는데, 그 함수는
        **파일 목록에 선택이 남아 있으면 파일 이름 칸의 텍스트를 무시한다.**
        Qt 는 글자를 칠 때마다(``_q_autoCompleteFileName``) 선택을 텍스트에 맞춰
        갱신해 이를 막는데, 그 호출을 건너뛰면 직전에 들어간 폴더의 선택이 남아
        **다른 경로를 쳐도 그 선택으로 확정**된다(= 이동이 안 된다).

        선택 항목의 경로는 모델에 이미 들어 있는 문자열이라 **파일시스템을
        건드리지 않고** 비교할 수 있다. 텍스트가 가리키는 경로와 다를 때만
        지우므로, 목록에서 클릭해 고른 선택은 그대로 남는다.

        지울 때는 **시그널을 막고** 지운다. 선택이 바뀌면 Qt 가 버튼 상태를
        다시 계산하면서 입력 경로를 ``access()`` 하는데, 그게 automount 를
        부르기 때문이다(우리가 막으려던 바로 그 호출). 화면의 하이라이트는
        시그널 없이 갱신되지 않으므로 뷰포트만 다시 그리게 한다.
        """
        wanted = {
            path
            for path in (
                _typed_path(self._dialog, part) for part in _split_typed(text)
            )
            if path
        }
        for name, kind in (("listView", QListView), ("treeView", QTreeView)):
            view = self._dialog.findChild(kind, name)
            model = view.selectionModel() if view is not None else None
            if model is None:
                continue
            current = {
                abspath(index.data(PATH_ROLE))
                for index in model.selectedIndexes()
                if index.column() == 0
            }
            current.discard(None)
            if not current or current == wanted:
                continue
            blocked = model.blockSignals(True)
            try:
                model.clearSelection()
            finally:
                model.blockSignals(blocked)
            view.viewport().update()

    def _update_accept_python(self, text):
        """Qt6 — 내부 슬롯이 없어 열기/저장 버튼 활성 판정을 직접 한다.

        Qt 원본 규칙의 근사다: 있는 파일(들)만 열 수 있고, 저장은 이름만 있으면
        되고, 폴더 모드는 폴더여야 한다. 판정이 어긋나 버튼이 살아 있어도
        ``QFileDialog.accept()`` 가 확정 시점에 다시 검증하므로 안전하다.
        존재 확인은 safe_* 라 죽은 마운트에서도 멈추지 않는다.
        """
        mode = self._dialog.fileMode()
        text = (text or "").strip()
        if not text:
            enabled = mode == enum_value("FileMode", "Directory")
        elif mode == enum_value("FileMode", "Directory"):
            enabled = safety.safe_isdir(_typed_path(self._dialog, text))
        elif mode in (
            enum_value("FileMode", "ExistingFile"),
            enum_value("FileMode", "ExistingFiles"),
        ):
            paths = [_typed_path(self._dialog, t) for t in _split_typed(text)]
            enabled = bool(paths) and all(safety.safe_exists(p) for p in paths if p)
        else:                           # AnyFile (저장)
            enabled = True
        # 이 경로에서는 Qt 가 선택을 갱신해 주지 않으므로 우리가 맞춘다
        self._sync_selection(text)
        self._enable_accept(bool(enabled))

    def _enable_accept(self, enabled):
        box = self._dialog.findChild(QDialogButtonBox, "buttonBox")
        if box is None:
            return
        save = self._dialog.acceptMode() == enum_value("AcceptMode", "AcceptSave")
        button = box.button(
            getattr(QDialogButtonBox.StandardButton, "Save" if save else "Open")
        )
        if button is not None:
            button.setEnabled(enabled)


def guard_dialog(dialog, bounce=True):
    """차단 경로를 나열하지도, 만지지도, 들어가지도 못하게 막는다.

    - 파일 이름 칸의 자동완성 모델을 :class:`GuardedFileSystemModel` 로 바꾼다
    - 키 입력마다 도는 자동 경로 확인을 위험한 자리에서 끈다 (:class:`_TypingGuard`)
    - 파일 목록에서 더블클릭/Enter 로 여는 것을 막는다
    - "Look in" 드롭다운에서 고르는 것을 막는다
    - 파일 이름 칸에 치고 Enter / 열기 버튼으로 확정하는 것을 막는다
    - ``bounce`` 가 True 면 그래도 들어가진 경우 직전 폴더로 되돌린다
      (이미 읽은 뒤라 늦지만, 그 자리에 머무르지는 않게 하는 마지막 방어)

    무엇이 걸리는지는 설정에 따라 다르다.

    - 설정 없이 시스템에 autofs 마운트만 있음 → 자동완성 모델 + 타이핑 가드
    - ``allow_listing=False`` 만 → 위와 같음 (나열만 막는 설정이므로)
    - ``min_depth`` → 위에 더해 **확정 차단** (깊이 ≤ min_depth 는 Enter · 열기
      버튼으로도 못 연다 — 확정의 stat 한 번이 automount 를 부른다)
    - ``guarded_roots`` → 전부 (더블클릭 · "Look in" · 확정 · 마지막 방어)

    걸 이유가 하나도 없으면 아무 일도 하지 않는다.

    Returns:
        건 장치들의 목록(진단용). 걸 게 없으면 빈 리스트.
    """
    guarded = safety.guarded_roots()
    if (
        not guarded
        and not safety.min_depth()
        and safety.listing_allowed()
        and not safety.has_automounts()
    ):
        return []

    # 이미 걸린 다이얼로그에 또 걸지 않는다. 두 번 걸면 이벤트 필터가 둘이 되어
    # 안내 팝업이 두 번 뜨고, 두 번째 타이핑 가드가 첫 번째의 연결을 끊어
    # 고아를 남긴다. (install_hooks 가 걸어 준 뒤 직접 부르는 경우가 있다.)
    if dialog.findChild(_TypingGuard) is not None:
        return []

    installed = []
    name_edit = dialog.findChild(QLineEdit, "fileNameEdit")

    # 1) 파일 이름 칸 자동완성 (min_depth 만 켜도 여기까지는 해 준다)
    completer = name_edit.completer() if name_edit is not None else None
    if completer is not None and not isinstance(
        completer.model(), GuardedFileSystemModel
    ):
        model = GuardedFileSystemModel(dialog)
        model.setRootPath("")
        completer.setModel(model)
        installed.append("completer")

    # 2) 키 입력마다 입력 경로를 access()/stat() 하는 Qt 내부 동작 차단
    typing_guard = _TypingGuard.install(dialog, name_edit)
    if typing_guard is not None:
        installed.append(typing_guard)

    if not guarded and not safety.min_depth():
        return installed        # 아래는 "확정하거나 들어가지 못하게" 하는 장치들이다

    if guarded:
        # 3) 파일 목록에서 열기 차단 (더블클릭)
        for view in (
            dialog.findChild(QTreeView, "treeView"),
            dialog.findChild(QListView, "listView"),
        ):
            if view is None:
                continue
            blocker = _ItemBlocker(
                view, (QEvent.Type.MouseButtonDblClick,), parent=dialog
            )
            view.viewport().installEventFilter(blocker)
            view.installEventFilter(blocker)
            installed.append(blocker)

        # 4) "Look in" 드롭다운에서 고르기 차단 (클릭 = release)
        combo = dialog.findChild(QComboBox, "lookInCombo")
        if combo is not None:
            view = combo.view()
            blocker = _ItemBlocker(
                view, (QEvent.Type.MouseButtonRelease,), combo.hidePopup, parent=dialog
            )
            view.viewport().installEventFilter(blocker)
            view.installEventFilter(blocker)
            installed.append(blocker)

    # 5) 파일 이름 칸으로 확정하기 차단 (Enter · 열기 버튼).
    #    guarded 는 물론 min_depth 만 켜도 건다 — Enter 의 확정은 Qt 가 GUI
    #    스레드에서 입력 경로를 stat 하는 일이라, automount 의 얕은 경로에서는
    #    그 한 번으로 멈춘다 (safety.may_open 이 판정).
    if name_edit is not None:
        blocker = _AcceptBlocker(dialog, name_edit, dialog)
        name_edit.installEventFilter(blocker)
        box = dialog.findChild(QDialogButtonBox, "buttonBox")
        for standard in ("Open", "Save"):
            button = box.button(getattr(QDialogButtonBox.StandardButton, standard)) if box else None
            if button is not None:
                button.installEventFilter(blocker)
        installed.append(blocker)

    # 6) 마지막 방어 (guarded 전용 — 이동 자체는 guarded 로만 막는다)
    if bounce and guarded:
        last = {"dir": dialog.directory().absolutePath()}

        def on_entered(path):
            if safety.is_guarded(path):
                dialog.setDirectory(last["dir"])
            else:
                last["dir"] = path

        dialog.directoryEntered.connect(on_entered)
        installed.append("bounce")

    return installed


