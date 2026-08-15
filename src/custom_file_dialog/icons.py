"""사이드바 항목에 씌울 아이콘을 그린다 (외부 이미지 파일 없이).

즐겨찾기 분류는 **별표**, 최근 파일은 **시계**, 홈은 **집**. 모두 같은 방식으로
여러 크기를 한 :class:`QIcon` 에 담아 두어 사이드바(16px)든 목록(24px)이든
또렷하게 보인다.
``QPixmap`` 을 쓰므로 ``QApplication`` 이 만들어진 뒤에 호출해야 한다.
"""

import math
import os

from qtpy.QtCore import QFileInfo, QMimeDatabase, QPointF, QStandardPaths, Qt
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
#
# 열쇠는 확장자가 아니라 **종류 이름**(``text/plain``)이다. 확장자를 그대로
# 쓰면 날짜·버전이 든 이름(``로그.2024.01.txt``)이 파일마다 다른 사슬이 되어
# 열쇠가 파일 수만큼 늘고, 그러면 캐시가 하는 일이 없다 — 실측: 날짜가 박힌
# 파일 2,000개에서 질의 2,000회 · 열쇠 2,000개였고, **다시 열어도 또 2,000회**
# 였다. 종류로 접으면 같은 폴더가 질의 1회 · 열쇠 1개가 된다.
_plain_icons = {}

# 확장자 사슬 -> 종류 이름. 이쪽은 이름에서 나오므로 계속 자랄 수 있어
# 상한을 둔다. 값이 문자열이라 아이콘보다 훨씬 가볍다.
_mime_names = {}
MAX_ICON_KEYS = 512

_mime_db = None


def _mime_name(suffix):
    """확장자 사슬로 본 종류 이름 — Qt 가 아이콘을 고르는 기준과 같다.

    실제 파일을 건드리지 않으려고 ``x.<확장자>`` 라는 **가짜 이름**만 넘기고
    확장자만 보게 한다(``MatchExtension``). 내용을 읽지 않으므로 네트워크
    왕복이 없고, 없는 경로·끊긴 링크에도 그대로 쓸 수 있다.
    """
    global _mime_db
    name = _mime_names.get(suffix)
    if name is None:
        if _mime_db is None:
            _mime_db = QMimeDatabase()
        if len(_mime_names) >= MAX_ICON_KEYS:
            _mime_names.clear()
        match = scoped_attr(QMimeDatabase, "MatchMode", "MatchExtension")
        name = _mime_names[suffix] = _mime_db.mimeTypeForFile(
            "x." + suffix if suffix else "x", match).name()
    return name


# Qt 가 전용 아이콘을 줄 수 있는 폴더들. 바인딩·Qt 판마다 있고 없는 이름이
# 있어 있는 것만 쓴다.
_SPECIAL_DIR_NAMES = (
    "HomeLocation", "DesktopLocation", "DocumentsLocation", "DownloadLocation",
    "MusicLocation", "PicturesLocation", "MoviesLocation", "TempLocation",
    "PublicShareLocation", "TemplatesLocation",
)

_special_dirs = None


def _special_dir(path):
    """``path`` 가 특수 폴더면 그 경로를, 아니면 빈 문자열.

    평범한 폴더를 **한 칸에 모으기 위한** 함수다. 목록은 프로세스에 한 번만
    만든다(``QStandardPaths`` 가 이미 캐시하지만, 폴더마다 부르면 그 자체가
    비용이다). 파일 시스템을 건드리지 않으므로 네트워크 왕복이 없다.
    """
    global _special_dirs
    if _special_dirs is None:
        _special_dirs = set()
        for name in _SPECIAL_DIR_NAMES:
            location = getattr(QStandardPaths, "StandardLocation", QStandardPaths)
            value = getattr(location, name, None)
            if value is None:
                continue
            found = QStandardPaths.writableLocation(value)
            if found:
                _special_dirs.add(found.rstrip("/") or "/")
    normal = path.rstrip("/") or "/"
    return normal if normal in _special_dirs else ""


def _cached(store, key, make):
    icon = store.get(key)
    if icon is None:
        icon = store[key] = make()
    return icon


def _color_key(color):
    """색을 **값**으로 바꾼다 — 캐시 열쇠로 쓰려면 이래야 한다.

    ``str(QColor)`` 를 쓰면 PyQt 에서는 값이 아니라 **객체 주소**가 나온다
    (``<PyQt5.QtGui.QColor object at 0x…>``). CPython 이 주소를 재사용하므로
    팔레트에서 색을 뽑아 아이콘을 여러 개 만들면 **먼저 그린 색이 그대로
    돌아왔다**(실측: PyQt5 5개 중 4개가 틀린 색. PySide 는 str 이 값 표현이라
    우연히 맞았다). 알파까지 담기게 ``#AARRGGBB`` 로 쓴다.
    """
    return QColor(color).name(QColor.NameFormat.HexArgb)


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

    return _cached(_drawn, ("star_icon", _color_key(color), tuple(sizes), inset),
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

    return _cached(_drawn, ("clock_icon", _color_key(color), tuple(sizes), inset),
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

    return _cached(_drawn, ("home_icon", _color_key(color), tuple(sizes), inset),
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
        묻는다(위 274개 -> 실제 질의 10회). 열쇠는 :meth:`_icon_key` 가 정한다.

        맞바꾼 것: 같은 종류인 **파일**들이 내용과 무관하게 같은 아이콘을 받는다.
        Qt 자신도 그 조회가 네트워크에서 비싸다고 보고 끄는 옵션
        (``DontUseCustomDirectoryIcons``)을 두고 있다.
        """
        key = self._icon_key(info)
        if key is None:
            return super().icon(info)
        return _cached(_plain_icons, key, lambda: super(
            CategoryIconProvider, self).icon(info))

    @staticmethod
    def _icon_key(info):
        """캐시 열쇠. **None 이면 캐시하지 말라는 뜻**(그때는 Qt 에 그냥 묻는다).

        지금은 그런 자리가 없지만, 규칙을 넓힐 때를 위해 열어 둔다.

        **폴더는 특수 폴더인지로 가른다.** Qt6 은 홈과 바탕화면에 XDG 전용
        아이콘을 주므로(실측: Qt6 + gtk3 에서만, 그리고 그 둘뿐. Qt5 는 전부
        같다) 폴더를 한 칸에 묶으면 먼저 물어본 것이 뒤엣것을 덮어써서 평범한
        폴더가 **바탕화면 모양**이 된다. 그렇다고 이름을 통째로 열쇠에 넣으면
        열쇠가 폴더 수만큼 늘어 캐시가 죽는다 — 네트워크 홈은 폴더가 대부분이라
        하필 가장 비싼 자리에서 그렇게 된다. :func:`_special_dir` 이 그 사이를
        가른다: 특수 폴더면 제 경로, 아니면 빈 문자열(= 평범한 폴더 한 칸).
        늘어나는 열쇠는 :data:`_SPECIAL_DIR_NAMES` 개수가 상한이고 추가 조회는
        없다.

        파일은 **종류 이름**으로 접는다 — Qt 가 아이콘을 고르는 기준 그대로다.
        확장자 사슬을 열쇠로 쓰던 예전 방식은 :func:`_mime_name` 에 적어 둔
        이유로 버렸다.

        특수 파일(FIFO · 소켓 · 장치)은 이름을 보지 않고 한 칸에 모은다 —
        Qt 도 이름이 아니라 종류로 아이콘을 주기 때문이다. 반대로 **끊긴 링크와
        없는 경로는 이름으로 갈린다**(``끊긴.txt`` -> text/plain,
        ``끊긴.png`` -> image/png). 이 둘을 가르는 것이 ``exists()`` 다.
        즐겨찾기·최근 파일 폴더는 안이 전부 링크이고 대상이 지워지면 끊긴
        링크가 되므로, 하필 이 라이브러리가 만드는 화면에서 드러난다.

        ``QFileInfo`` 가 이 답들을 이미 들고 있어(항목을 그리려고 stat 한 결과)
        추가 비용은 없다.
        """
        if info.isRoot():
            return ("root", info.absoluteFilePath())
        if info.isDir():
            # 링크 여부는 Qt 가 겹쳐 그리는 화살표를 가른다 — 폴더도 마찬가지다.
            return ("dir", info.isSymLink(),
                    _special_dir(info.absoluteFilePath()))
        if info.exists() and not info.isFile():
            return ("special",)     # FIFO · 소켓 · 장치 -> 이름을 안 본다
        # 심볼릭 링크 여부는 Qt 가 겹쳐 그리는 화살표를 가른다.
        return (info.isSymLink(), _mime_name(CategoryIconProvider._suffix(info)))

    @staticmethod
    def _suffix(info):
        """이름에서 본 확장자 — **첫 점 뒤 전부**, 대소문자 그대로.

        세 가지가 다 이유가 있다(전부 실측으로 확인했다).

        - **첫 점 뒤 전부**를 본다. 마지막 점만 보면 ``묶음.tar.gz`` 와
          ``그냥.gz`` 가 같은 칸인데 Qt 는 다르게 본다
          (x-compressed-tar vs gzip).
        - **소문자로 바꾸지 않는다.** ``소스.c``(text/x-csrc) 와
          ``소스.C``(text/x-c++src) 는 Qt 에서 다른 종류다.
        - **맨 앞 점은 확장자가 아니다.** 점파일(``.bashrc``)은 확장자가 없는
          것으로 본다.

        여기서 나온 사슬은 :func:`_mime_name` 이 곧바로 종류 이름으로 접으므로,
        사슬이 몇 가지든 열쇠 수는 종류 수를 넘지 않는다.
        """
        name = info.fileName()
        dot = name.find(".", 1)         # 점파일의 맨 앞 점은 확장자가 아니다
        return name[dot + 1:] if dot > 0 else ""

