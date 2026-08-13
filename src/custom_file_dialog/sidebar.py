"""사이드바를 보기 좋게 손질한다 — 항목 표시와 폭.

Qt 는 사이드바를 **경로 이름 그대로, 고정 폭으로** 그린다. 그래서 두 가지를
손본다.

- **표시** — 홈은 집 아이콘으로, 다이얼로그가 열리는 자리는 "현재 위치" 라는
  이름으로 보여 준다 (:func:`mark_sidebar`).
- **폭** — 항목이 잘리지 않을 만큼 넓힌다 (:func:`fit_sidebar`).

둘 다 **표시만** 바꾼다. 항목이 가리키는 경로는 그대로여서 클릭했을 때 열리는
곳은 달라지지 않는다.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QListView, QSplitter, QStyle, QStyledItemDelegate

from .constants import PATH_ROLE
from .qt_compat import scoped_attr
from .util import abspath, url_path

# 사이드바 항목(QUrlModel)이 "이 위치를 열 수 있는가"를 담는 역할. Qt5/Qt6 동일.
ENABLED_ROLE = Qt.ItemDataRole.UserRole + 2

_STATE_ENABLED = scoped_attr(QStyle, "StateFlag", "State_Enabled")

# 폭을 항목에 맞출 때 글자 오른쪽에 남기는 여백(px)
SIDEBAR_PADDING = 8

# 사이드바 최소 폭(px). 설정이 하나도 없는 새 프로필에서 Qt 는 79px 로 여는데
# 너무 좁다. 한 번이라도 다이얼로그를 닫으면 Qt 가 저장하는 값(115)에 맞춘다.
MIN_SIDEBAR_WIDTH = 115

# 사이드바를 넓히더라도 파일 목록에 최소한 남겨 둘 폭(px)
MIN_FILE_LIST_WIDTH = 260


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



def fit_sidebar(dialog, width=None):
    """사이드바 폭을 정한다.

    ``None`` 이면 항목이 잘리지 않을 만큼 넓히고, ``0`` 이면 내용에는 맞추지
    않는다. 둘 다 :data:`MIN_SIDEBAR_WIDTH` 아래로는 내려가지 않는다.
    숫자를 주면 그 값을 그대로 쓴다.

    **다이얼로그가 보인 뒤에 불러야 한다.** 스플리터 크기는 보이기 전에는
    정해지지 않아서(생성 직후엔 ``[0, 0]``) 그 전에는 아무 일도 하지 않는다.

    Returns:
        정해진 폭. 손대지 못했으면 None.
    """
    splitter = dialog.findChild(QSplitter, "splitter")
    sidebar = dialog.findChild(QListView, "sidebar")
    if splitter is None or sidebar is None:
        return None
    sizes = splitter.sizes()
    total = sum(sizes)
    if len(sizes) < 2 or total <= 0:
        return None                     # 아직 자리가 안 잡혔다

    if width is None or width == 0:
        floor = MIN_SIDEBAR_WIDTH
        if width is None:               # 항목이 잘리지 않게
            floor = max(
                floor,
                sidebar.sizeHintForColumn(0)
                + 2 * sidebar.frameWidth()
                + SIDEBAR_PADDING,
            )
        # 이미 충분히 넓으면 그대로 둔다(좁히지 않는다)
        width = max(sizes[0], floor)

    # 넓히더라도 파일 목록 자리는 남겨 둔다. 다만 그 상한이 최소 폭을 먹지
    # 않게 한다 — 창이 좁아도 사이드바는 MIN_SIDEBAR_WIDTH 를 지킨다(문서한 값).
    room = max(0, total - MIN_FILE_LIST_WIDTH)
    width = max(0, min(int(width), max(room, MIN_SIDEBAR_WIDTH)))
    if not width:
        return None                     # 손대지 못했다 — 다음 show 때 다시 본다
    if width != sizes[0]:
        splitter.setSizes([width, max(0, total - width)])
    return width
