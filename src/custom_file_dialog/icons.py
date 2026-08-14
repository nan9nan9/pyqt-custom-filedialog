"""사이드바 항목에 씌울 아이콘을 그린다 (외부 이미지 파일 없이).

즐겨찾기 분류는 **별표**, 최근 파일은 **시계**, 홈은 **집**. 모두 같은 방식으로
여러 크기를 한 :class:`QIcon` 에 담아 두어 사이드바(16px)든 목록(24px)이든
또렷하게 보인다.
``QPixmap`` 을 쓰므로 ``QApplication`` 이 만들어진 뒤에 호출해야 한다.
"""

import math
import os

from qtpy.QtCore import QFileInfo, QPointF, Qt
from qtpy.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from qtpy.QtWidgets import QFileIconProvider, QStyle

from .qt_compat import scoped_attr
from .util import abspath

# 만들어 둘 크기들
ICON_SIZES = (16, 20, 24, 32, 48)

# 반지름에서 빼는 픽셀 수. 지름 기준으로는 두 배(=2px)만큼 작아져,
# 옆 텍스트·폴더 아이콘과 나란히 놓았을 때 혼자 커 보이지 않는다.
INSET = 1.0

STAR_COLOR = "#f9a825"      # 즐겨찾기 (골드)
CLOCK_COLOR = "#1e88e5"     # 최근 파일 (파랑)
HOME_COLOR = "#43a047"      # 홈 (초록)

# 집 모양의 꼭짓점 — 한 변이 1 인 정사각형 기준(왼쪽 위가 0, 0).
# 지붕 꼭대기에서 시계 방향으로 돌고, 아래쪽 가운데는 문으로 파여 있다.
_HOUSE = (
    (0.50, 0.02), (1.00, 0.46), (0.86, 0.46), (0.86, 0.98),
    (0.61, 0.98), (0.61, 0.60), (0.39, 0.60), (0.39, 0.98),
    (0.14, 0.98), (0.14, 0.46), (0.00, 0.46),
)


def _draw(sizes, paint):
    """크기마다 투명 픽스맵을 만들어 ``paint(painter, size)`` 로 그린다."""
    icon = QIcon()
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint(painter, size)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _radius(size, inset):
    return max(1.0, size / 2.0 * 0.94 - inset)


def _point(center, length, degrees):
    """중심에서 ``degrees`` 방향(12시가 0도)으로 ``length`` 만큼 간 점."""
    angle = math.radians(degrees - 90)
    return QPointF(center + length * math.cos(angle), center + length * math.sin(angle))


def star_icon(color=STAR_COLOR, sizes=ICON_SIZES, inset=INSET):
    """즐겨찾기 분류에 쓸 별표 아이콘."""
    brush = QColor(color)

    def paint(painter, size):
        center, outer = size / 2.0, _radius(size, inset)
        polygon = QPolygonF()
        for step in range(10):                      # 꼭짓점 5개 = 바깥/안쪽 10점
            length = outer if step % 2 == 0 else outer * 0.42
            polygon.append(_point(center, length, step * 36))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)
        painter.drawPolygon(polygon)

    return _draw(sizes, paint)


def clock_icon(color=CLOCK_COLOR, sizes=ICON_SIZES, inset=INSET):
    """최근 파일에 쓸 시계 아이콘."""
    pen_color = QColor(color)

    def paint(painter, size):
        center = size / 2.0
        width = max(1.0, size / 12.0)
        inner = _radius(size, inset) - width / 2.0  # 선 굵기의 절반만큼 안쪽

        pen = painter.pen()
        pen.setColor(pen_color)
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawEllipse(QPointF(center, center), inner, inner)
        painter.drawLine(QPointF(center, center), _point(center, inner * 0.50, 330))
        painter.drawLine(QPointF(center, center), _point(center, inner * 0.72, 120))

    return _draw(sizes, paint)


def home_icon(color=HOME_COLOR, sizes=ICON_SIZES, inset=INSET):
    """홈 위치에 쓸 집 아이콘."""
    brush = QColor(color)

    def paint(painter, size):
        # 별표·시계의 지름과 같은 폭에 맞춰 나란히 놓아도 크기가 고르게 보인다
        span = max(1.0, _radius(size, inset) * 2.0)
        origin = (size - span) / 2.0
        polygon = QPolygonF()
        for x, y in _HOUSE:
            polygon.append(QPointF(origin + x * span, origin + y * span))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)
        painter.drawPolygon(polygon)

    return _draw(sizes, paint)


def standard_icon(widget, name):
    """QStyle 표준 아이콘을 바인딩에 무관하게 얻는다 (없으면 None)."""
    try:
        pixmap = scoped_attr(QStyle, "StandardPixmap", name)
    except AttributeError:
        return None
    return widget.style().standardIcon(pixmap)


class CategoryIconProvider(QFileIconProvider):
    """분류 폴더에만 전용 아이콘을 씌우는 아이콘 제공자.

    :meth:`QFileDialog.setIconProvider` 로 걸면 사이드바와 파일 목록 양쪽에서
    분류 폴더가 그 아이콘으로 표시된다. 저장소를 여러 개 걸 수 있어 즐겨찾기는
    별표, 최근 파일은 시계처럼 나눠 줄 수 있다::

        provider = CategoryIconProvider()
        provider.add_store(favorites)               # 별표(기본)
        provider.add_store(recent, clock_icon())    # 시계

    주의: ``setIconProvider()`` 는 소유권을 가져가지 않으므로, 다이얼로그가 살아
    있는 동안 이 객체의 참조를 어딘가에 유지해야 한다.
    """

    def __init__(self, store=None, icon=None):
        super().__init__()
        self._entries = []          # [(저장소, 아이콘 또는 None)]
        self._bases = set()         # 저장소 뿌리들 — "/" 구분자로 통일한 절대 경로
        self._star = None
        if store is not None:
            self.add_store(store, icon)

    def add_store(self, store, icon=None):
        """분류 폴더를 알아볼 저장소를 추가한다(icon 이 None 이면 별표)."""
        if store is not None:
            self._entries.append((store, icon))
            base = abspath(store.base_dir)
            if base:
                self._bases.add(base.replace(os.sep, "/"))
        return self

    def star(self):
        """기본 별표 아이콘 (처음 쓸 때 그린다)."""
        if self._star is None:
            self._star = star_icon()
        return self._star

    def icon(self, arg):        # noqa: A003 (Qt 시그니처)
        # icon(QFileInfo) 와 icon(IconType) 두 가지 오버로드가 들어온다.
        #
        # QFileSystemModel 이 **목록의 항목마다** 부르는 함수라, 큰 폴더에서는
        # 여기서 쓰는 시간이 그대로 나열 시간에 더해진다. 분류 폴더일 수 있는
        # 항목(부모가 저장소 뿌리)만 문자열 비교 하나로 먼저 골라내고, 경로
        # 정규화와 저장소 질의는 그때만 한다. 대부분의 항목은 Qt 기본 아이콘
        # 경로로 바로 넘어간다.
        if isinstance(arg, QFileInfo):
            path = arg.absoluteFilePath()       # Qt 는 늘 "/" 구분자를 쓴다
            if path.rsplit("/", 1)[0] in self._bases:
                # 폴더인지는 **Qt 가 이미 안다** — 항목을 그리려고 stat 해서
                # 채워 둔 QFileInfo 를 들고 우리를 부르기 때문이다. 그 답을
                # 넘겨 주면 저장소 쪽이 같은 stat 을 다시 하지 않는다
                # (네트워크 저장소에서는 그 왕복이 그대로 목록 지연이다).
                is_dir = arg.isDir()
                for store, icon in self._entries:
                    if store.is_category_dir(path, is_dir=is_dir):
                        return icon if icon is not None else self.star()
        return super().icon(arg)
