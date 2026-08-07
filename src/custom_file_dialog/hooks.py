"""QFileDialog 에 거는 것들 — 사이드바 표시, 링크 추적, 차단 경로 방어.

즐겨찾기·최근 파일은 심볼릭 링크를 모아 둔 폴더라 두 가지 손질이 필요하다.

- **사이드바 표시** — 홈은 집 아이콘으로, 다이얼로그가 열리는 자리는
  "현재 위치"라는 이름으로 보여 준다.
- **링크 추적** — 목록에서 항목을 고르면 "Look in" 에 원본 경로를 보여 주고,
  링크 폴더로 들어가면 원본 폴더로 옮기고, "상위 폴더"로 올라가면 링크 창고가
  아니라 원본이 있는 폴더로 간다.
- **차단 경로 방어** — ``/user`` 처럼 나열하는 것만으로 시스템이 주저앉는 자리는
  자동완성·목록·드롭다운·파일 이름 칸 어디서도 열 수 없게 막는다
  (:func:`~custom_file_dialog.safety.is_guarded`).

Qt 가 C++ 에서 연결해 둔 ``activated`` · ``accept()`` 는 파이썬에서 끊을 수 없다.
그래서 **그 신호가 나기 전 단계인 입력 이벤트를 삼키는** 방식으로 막는다.

:func:`install_hooks` 가 메뉴까지 포함해 한 번에 걸어 준다.
"""

import os

from qtpy.QtCore import QEvent, QObject, Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileSystemModel,
    QLineEdit,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QTreeView,
)

from . import safety
from .constants import PATH_ROLE
from .menus import FavoritesMenus
from .util import abspath, url_path

_OPEN_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)

# 사이드바 항목(QUrlModel)이 "이 위치를 열 수 있는가"를 담는 역할. Qt5/Qt6 동일.
ENABLED_ROLE = Qt.ItemDataRole.UserRole + 2

# 바인딩마다 enum 스코프가 달라 icons.standard_icon 과 같은 방식으로 집는다.
_STATE_ENABLED = getattr(QStyle, "StateFlag", QStyle).State_Enabled


# --------------------------------------------------------- 사이드바 표시
class _SidebarMarks(QStyledItemDelegate):
    """사이드바 항목의 이름과 아이콘만 갈아 끼우는 델리게이트.

    모델을 직접 고치면 안 된다. ``QUrlModel`` 은 파일시스템이 바뀌었다는 통지를
    받을 때마다 항목의 이름과 아이콘을 **경로에서 다시 읽어** 덮어쓰므로, 넣어 둔
    값이 몇 초 만에 사라진다. 그래서 모델은 그대로 두고 **그리기 직전**에
    바꿔치기한다.

    Qt 기본 델리게이트가 하던 "열 수 없는 위치는 흐리게" 처리도 이어서 한다.
    """

    def __init__(self, marks, parent=None):
        super().__init__(parent)
        self.marks = marks

    def initStyleOption(self, option, index):    # noqa: N802 (Qt 시그니처)
        super().initStyleOption(option, index)

        enabled = index.data(ENABLED_ROLE)
        if enabled is not None and not enabled:
            option.state &= ~_STATE_ENABLED

        label, icon = self.marks.get(
            abspath(url_path(index.data(PATH_ROLE))), (None, None)
        )
        if label:
            option.text = label
        if icon is not None:
            option.icon = icon


def mark_sidebar(dialog, places, current=None):
    """사이드바의 홈에 집 아이콘을, 현재 위치 항목에 "현재 위치" 이름을 씌운다.

    Args:
        dialog: 대상 ``QFileDialog`` (Qt 자체 다이얼로그여야 한다).
        places: :class:`~custom_file_dialog.places.Places`.
        current: 다이얼로그가 열리는 폴더. None 이면 다이얼로그에서 읽는다.

    Returns:
        걸어 둔 델리게이트. 씌울 것이 없거나 사이드바를 못 찾으면 None.
    """
    if current is None:
        current = dialog.directory().absolutePath()
    marks = places.sidebar_marks(current)
    if not marks:
        return None

    sidebar = dialog.findChild(QListView, "sidebar")
    if sidebar is None:
        return None

    # 부모는 사이드바가 아니라 **다이얼로그**여야 한다. 사이드바는 findChild 로
    # 그때그때 만들어지는 임시 래퍼라, 거기에 매달아 두면 래퍼가 사라질 때
    # 파이썬 쪽 객체도 같이 수거되어 재정의한 initStyleOption 이 불리지 않는다
    # (C++ 객체만 남아 Qt 기본 델리게이트처럼 동작한다).
    delegate = _SidebarMarks(marks, dialog)
    sidebar.setItemDelegate(delegate)
    return delegate


# ----------------------------------------------------- 즐겨찾기 링크 추적
def show_link_target_in_combo(dialog, places):
    """항목을 고르면 "Look in" 콤보에 그 항목의 **실제 위치**를 보여 준다.

    **폴더를 옮기지는 않는다.** 표시만 바꾸므로 목록은 분류에 그대로 머문다.
    폴더를 이동하면 Qt 가 콤보를 다시 채우므로 표시도 저절로 되돌아온다.
    """
    combo = dialog.findChild(QComboBox, "lookInCombo")
    if combo is None:
        return False

    def on_current_changed(path):
        index = combo.currentIndex()
        if index >= 0:
            # 링크가 아니면 현재 폴더 경로로 되돌린다(앞서 바꿔 놨을 수 있으므로)
            combo.setItemText(
                index, places.link_target(path) or dialog.directory().absolutePath()
            )

    dialog.currentChanged.connect(on_current_changed)
    return True


def follow_link_on_parent(dialog, places):
    """분류 폴더에서 링크를 고르고 **"상위 폴더"** 를 누르면 원본 위치로 올라간다.

    Qt 는 지금 보고 있는 폴더(=분류 폴더)의 부모로 올라간다. 그래서 링크를 모아 둔
    저장소 안쪽으로 들어가 버리는데, 거기는 분류 폴더만 늘어선 창고라 열어 봐야
    쓸 것이 없다. "Look in" 이 이미 가리키고 있는 **원본 경로**를 기준으로 올라가,
    고른 것이 실제로 있는 폴더로 가게 바로잡는다::

        <favorites>/설계/data.csv   (원본 /mnt/proj/설계문서/data.csv)
        "상위 폴더" ->  /mnt/proj/설계문서      (전에는 <favorites>)

    폴더 링크도 같은 규칙이다 — 원본이 **있는** 폴더로 간다.

    Qt 가 C++ 에서 연결해 둔 ``_q_navigateToParent`` 는 파이썬에서 끊을 수 없다.
    그래서 버튼이 눌린 순간에 목적지를 미리 정해 두었다가, Qt 가 옮긴 **직후에
    바로잡는다**. 잘못 들르는 저장소는 늘 로컬이라 오가는 비용이 없다.
    (Alt+Up 단축키도 이 버튼에 걸려 있어 함께 처리된다.)
    """
    button = dialog.findChild(QToolButton, "toParentButton")
    if button is None:
        return False

    state = {"target": None, "goto": None}

    def on_current_changed(path):
        state["target"] = places.link_target(path)

    def on_pressed():
        # 누른 그 순간의 상황으로 목적지를 정한다(누른 뒤에는 이미 옮겨진 뒤다)
        state["goto"] = None
        target = state["target"]
        if not target:
            return
        if not places.is_inside(dialog.directory().absolutePath()):
            return                       # 분류 폴더 안에서 고른 링크일 때만
        state["goto"] = os.path.dirname(abspath(target) or "")

    def on_entered(_path):
        destination, state["goto"], state["target"] = state["goto"], None, None
        # 원본이 죽은 마운트에 있으면 그냥 둔다(GUI 를 멈추느니 제자리가 낫다)
        if destination and safety.safe_isdir(destination):
            dialog.setDirectory(destination)

    dialog.currentChanged.connect(on_current_changed)
    button.pressed.connect(on_pressed)
    dialog.directoryEntered.connect(on_entered)
    return True


def follow_link_directories(dialog, places):
    """링크 폴더로 들어가면 원본 위치로 옮긴다.

    Qt 는 표시 경로에서 심볼릭 링크를 풀어 주지 않으므로, 그런 폴더에 들어간
    순간 원본으로 다시 보내 그 뒤로는 평범한 실제 경로로 탐색이 이어지게 한다.
    """

    def on_entered(path):
        target = places.link_target(path)
        if target is not None:
            # 프로그램이 부르는 setDirectory 는 directoryEntered 를 다시 내지
            # 않으므로 여기서 되돌아 들어오는 일은 없다.
            dialog.setDirectory(target)

    dialog.directoryEntered.connect(on_entered)
    return True


# --------------------------------------------------------- 차단 경로 방어
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


# --------------------------------------------------------------- 한 번에
def install_hooks(dialog, places, current=None):
    """다이얼로그에 사이드바 표시 · 링크 추적 · 메뉴 · 차단 경로 방어를 모두 건다.

    ``current`` 는 :func:`mark_sidebar` 로 넘어가는 "현재 위치"다. 주지 않으면
    다이얼로그가 지금 열려 있는 폴더를 쓴다.
    """
    guard_dialog(dialog)
    mark_sidebar(dialog, places, current)

    if not places.stores():
        return

    FavoritesMenus(dialog, places).install()
    follow_link_directories(dialog, places)      # 링크 폴더로 진입할 때
    show_link_target_in_combo(dialog, places)    # 목록에서 항목을 고를 때
    follow_link_on_parent(dialog, places)        # "상위 폴더" 로 올라갈 때
