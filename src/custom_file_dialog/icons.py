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


# 같은 모양·색·크기의 아이콘은 **프로세스에 하나만** 둔다.
#
# 이 아이콘들은 상태가 없어 다이얼로그마다 새로 그릴 이유가 없는데, 캐시가
# Places 인스턴스에 묶여 있어서 다이얼로그를 띄울 때마다 QIcon 5벌(픽스맵
# 25장)이 새로 그려졌다. PyQt 는 그것을 회수하지만 **PySide 는 못 한다** —
# 다이얼로그를 반복해 여는 앱에서 그대로 쌓였다(실측: PySide2 에서 회당
# 452KB · QIcon +9개, 50회에 22MB. 같은 조건에서 PyQt5 는 0.2KB).
# 그리는 비용도 매번 들었다(다이얼로그 생성 42.6ms 중 5벌).
_drawn = {}

# Qt 기본 아이콘도 마찬가지다 — 같은 종류면 어느 제공자가 묻든 답이 같으므로
# 제공자마다 따로 들 이유가 없다(그러면 다이얼로그 수만큼 쌓인다).
_plain_icons = {}


def _cached(store, key, make):
    icon = store.get(key)
    if icon is None:
        icon = store[key] = make()
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

    return _cached(_drawn, ("star_icon", str(color), tuple(sizes), inset),
                   lambda: _draw(sizes, paint))


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

    return _cached(_drawn, ("clock_icon", str(color), tuple(sizes), inset),
                   lambda: _draw(sizes, paint))


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

    return _cached(_drawn, ("home_icon", str(color), tuple(sizes), inset),
                   lambda: _draw(sizes, paint))


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
            return self._plain(arg)
        return super().icon(arg)

    def _plain(self, info):
        """Qt 기본 아이콘 — **종류마다 한 번만** 묻고 재사용한다.

        Qt 는 아이콘을 고르려고 종류를 알아내고(확장자가 없으면 파일 내용까지
        들여다본다) 아이콘 테마 폴더를 뒤진다. 그 테마 폴더에는 ``~/.icons`` ·
        ``~/.local/share/icons`` 가 들어 있어서, **홈이 네트워크에 있으면 항목
        하나하나가 서버 왕복이 된다** (실측: 항목당 4.9ms · 홈 274개에 1.3초).

        게다가 Qt 는 화면에 **보이지 않는 항목까지 전부** 훑는다 — 필터로 7개만
        보이는 폴더에서도 274번 불렸다. 종류가 같으면 아이콘도 같으므로 한 번만
        묻는다(위 274개 -> 실제 질의 10회).

        **열쇠는 Qt 가 종류를 가르는 기준 그대로여야 한다.** Qt 는 뿌리(Drive) ·
        폴더 · 파일 · 그 밖(FIFO · 소켓 · 장치 · 끊긴 링크 — 빈 아이콘)을 다르게
        보는데, 그것을 뭉뚱그리면 **먼저 물어본 것이 뒤엣것을 덮어쓴다.**

        - 뿌리 ``/`` 를 폴더와 같은 열쇠로 묶었더니, 모델이 시작 폴더의 인덱스를
          만들며 **루트부터** 묻는 바람에 그 디스크 아이콘이 박혀서 **모든 폴더가
          하드디스크 모양**이 됐다(4개 바인딩 전부 재현). 윈도우에서는 ``C:/``
          ``D:/`` 가 전부 이 자리다.
        - FIFO·소켓을 확장자 없는 파일과 묶었더니, 홈에 흔한 소켓
          (``.gnupg/S.gpg-agent``)이 먼저 나열되면 ``README``·``Makefile`` 같은
          평범한 파일이 **아이콘 없이** 그려졌다(반대로도 갈린다).

        심볼릭 링크 여부도 같은 이유로 넣는다 — 즐겨찾기·최근 파일 폴더는 안이
        **전부 링크**라 빼먹으면 하필 이 라이브러리가 만드는 화면에서 가장 잘
        보인다. 확장자를 뽑는 규칙은 :meth:`_suffix` 에 적어 두었다.
        점파일(``.bashrc``)은 확장자가 없는 것으로 본다 — 맨 앞 점을 확장자로
        보면 점파일마다 열쇠가 달라져 캐시가 듣지 않는다.

        ``QFileInfo`` 가 이 답들을 이미 들고 있어(항목을 그리려고 stat 한 결과)
        추가 비용은 없다.

        맞바꾼 것: 확장자가 없는 **파일**들이 내용과 무관하게 같은 아이콘을 받고,
        폴더마다 다른 아이콘을 두는 데스크톱 설정이 무시된다. Qt 자신도 그
        조회가 네트워크에서 비싸다고 보고 끄는 옵션
        (``DontUseCustomDirectoryIcons``)을 두고 있다.
        """
        key = (self._kind(info), info.isSymLink(), self._suffix(info))
        return _cached(_plain_icons, key, lambda: super(
            CategoryIconProvider, self).icon(info))

    @staticmethod
    def _kind(info):
        """Qt 가 아이콘을 고를 때 가르는 갈래 — 뿌리 · 폴더 · 파일 · 그 밖."""
        if info.isRoot():
            return "root"           # Qt 는 여기에 디스크 아이콘을 준다
        if info.isDir():
            return "dir"
        if info.isFile():
            return "file"
        return "other"              # FIFO · 소켓 · 장치 · 끊긴 링크 -> 빈 아이콘

    @staticmethod
    def _suffix(info):
        """이름에서 본 확장자 — **첫 점 뒤 전부**, 대소문자 그대로.

        세 가지가 다 이유가 있다(전부 실측으로 확인했다).

        - **파일이 아닐 때도 본다.** Qt 는 끊긴 링크와 없는 경로도 이름으로
          종류를 가린다(``끊긴.txt`` -> text/plain, ``끊긴.png`` -> image/png).
          파일일 때만 보면 그 둘이 한 칸에 묶여 먼저 물어본 쪽 아이콘을 받는다.
          즐겨찾기·최근 파일 분류 폴더는 안이 전부 링크이고 대상이 지워지면
          끊긴 링크가 되므로, 하필 이 라이브러리가 만드는 화면에서 드러난다.
        - **첫 점 뒤 전부**를 본다. 마지막 점만 보면 ``묶음.tar.gz`` 와
          ``그냥.gz`` 가 같은 칸인데 Qt 는 다르게 본다
          (x-compressed-tar vs gzip).
        - **소문자로 바꾸지 않는다.** ``소스.c``(text/x-csrc) 와
          ``소스.C``(text/x-c++src) 는 Qt 에서 다른 종류다.

        더 잘게 갈리는 대신(실측: 항목 310개에서 열쇠 16개 -> 22개) 틀린
        아이콘이 사라진다(틀린 열쇠 3개 -> 0개).
        """
        name = info.fileName()
        dot = name.find(".", 1)         # 점파일의 맨 앞 점은 확장자가 아니다
        return name[dot + 1:] if dot > 0 else ""

