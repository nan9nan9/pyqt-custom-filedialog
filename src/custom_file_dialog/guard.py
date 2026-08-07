"""나열하면 안 되는 자리를 다이얼로그가 건드리지 못하게 막는다.

``/user`` 처럼 아래에 automount 가 잔뜩 달린 자리는 **목록을 읽는 것만으로**
전부 마운트되면서 시스템이 주저앉는다. 목록을 읽게 만드는 통로를 모두 막는다.

- 자동완성 모델을 :class:`GuardedFileSystemModel` 로 갈아 끼운다
- 파일 목록에서 더블클릭/Enter 로 여는 것을 막는다
- "Look in" 드롭다운에서 고르는 것을 막는다
- 파일 이름 칸에 치고 확정하는 것을 막는다

무엇을 막을지는 :mod:`~custom_file_dialog.safety` 의 전역 설정
(``guarded_roots`` · ``min_depth`` · ``allow_listing``)이 정한다.

Qt 가 C++ 에서 연결해 둔 ``activated`` · ``accept()`` 는 파이썬에서 끊을 수 없다.
그래서 **그 신호가 나기 전 단계인 입력 이벤트를 삼키는** 방식으로 막는다.
"""

import os

from qtpy.QtCore import QEvent, QObject, Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileSystemModel,
    QLineEdit,
    QListView,
    QTreeView,
)

from . import safety
from .constants import PATH_ROLE
from .util import abspath, url_path

_OPEN_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class GuardedFileSystemModel(QFileSystemModel):
    """위험한 자리의 목록을 **아예 요청하지 않는** 파일시스템 모델.

    자식이 없다고 답해서 Qt 가 그 폴더를 읽지 않게 한다. 세 가지를 막는다
    (:func:`~custom_file_dialog.safety.may_list` 가 셋을 한 번에 판정한다).

    - ``guarded_roots`` 로 지목한 자리 (``/user``). 하위 경로(``/user/jekai``)는
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
    """파일 이름 칸에 차단 경로를 치고 확정하는 것을 막는다.

    ``QDialog.accept()`` 는 Qt 가 C++ 에서 부르므로 가로챌 수 없다. 그 앞 단계인
    **Enter 키와 열기 버튼 클릭**을 삼킨다.
    """

    def __init__(self, dialog, name_edit, parent=None):
        super().__init__(parent if parent is not None else dialog)
        self._dialog = dialog
        self._edit = name_edit
        self.blocked = []

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
        elif kind != QEvent.Type.MouseButtonRelease:
            return False

        # 상대 경로("..")도 현재 폴더 기준으로 풀어서 판정한다
        text = (self._edit.text() or "").strip()
        if not text:
            return False
        path = text if os.path.isabs(text) else os.path.join(
            self._dialog.directory().absolutePath(), text
        )
        path = abspath(path)
        if path and safety.is_guarded(path):
            self.blocked.append(path)
            return True
        return False


def guard_dialog(dialog, bounce=True):
    """차단 경로를 나열하지도, 들어가지도 못하게 막는다.

    - 파일 이름 칸의 자동완성 모델을 :class:`GuardedFileSystemModel` 로 바꾼다
    - 파일 목록에서 더블클릭/Enter 로 여는 것을 막는다
    - "Look in" 드롭다운에서 고르는 것을 막는다
    - 파일 이름 칸에 치고 Enter / 열기 버튼으로 확정하는 것을 막는다
    - ``bounce`` 가 True 면 그래도 들어가진 경우 직전 폴더로 되돌린다
      (이미 읽은 뒤라 늦지만, 그 자리에 머무르지는 않게 하는 마지막 방어)

    ``min_depth`` / ``allow_listing`` 만 켜 두었다면 **자동완성 모델만** 바꾼다.
    목록을 읽지 않게 하는 설정이지, 그 자리에 못 들어가게 하는 설정은 아니기
    때문이다. 셋 다 없으면 아무 일도 하지 않는다.

    Returns:
        건 장치들의 목록(진단용). 걸 게 없으면 빈 리스트.
    """
    guarded = safety.guarded_roots()
    if not guarded and not safety.min_depth() and safety.listing_allowed():
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

    if not guarded:
        return installed        # 아래는 "그 자리에 못 들어가게" 하는 장치들이다

    # 2) 파일 목록에서 열기 차단 (더블클릭)
    for view in (
        dialog.findChild(QTreeView, "treeView"),
        dialog.findChild(QListView, "listView"),
    ):
        if view is None:
            continue
        blocker = _ItemBlocker(view, (QEvent.Type.MouseButtonDblClick,), parent=dialog)
        view.viewport().installEventFilter(blocker)
        view.installEventFilter(blocker)
        installed.append(blocker)

    # 3) "Look in" 드롭다운에서 고르기 차단 (클릭 = release)
    combo = dialog.findChild(QComboBox, "lookInCombo")
    if combo is not None:
        view = combo.view()
        blocker = _ItemBlocker(
            view, (QEvent.Type.MouseButtonRelease,), combo.hidePopup, parent=dialog
        )
        view.viewport().installEventFilter(blocker)
        view.installEventFilter(blocker)
        installed.append(blocker)

    # 4) 파일 이름 칸으로 확정하기 차단 (Enter · 열기 버튼)
    if name_edit is not None:
        blocker = _AcceptBlocker(dialog, name_edit, dialog)
        name_edit.installEventFilter(blocker)
        box = dialog.findChild(QDialogButtonBox, "buttonBox")
        for standard in ("Open", "Save"):
            button = box.button(getattr(QDialogButtonBox.StandardButton, standard)) if box else None
            if button is not None:
                button.installEventFilter(blocker)
        installed.append(blocker)

    # 5) 마지막 방어
    if bounce:
        last = {"dir": dialog.directory().absolutePath()}

        def on_entered(path):
            if safety.is_guarded(path):
                dialog.setDirectory(last["dir"])
            else:
                last["dir"] = path

        dialog.directoryEntered.connect(on_entered)
        installed.append("bounce")

    return installed


