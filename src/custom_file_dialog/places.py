"""사이드바에 얹는 것들을 한 묶음으로 — 즐겨찾기 · 최근 파일 · 직접 지정 위치.

즐겨찾기와 최근 파일은 저장 방식이 같고(분류 폴더 + 심볼릭 링크) 늘 함께 다닌다.
그래서 "이 경로가 어느 분류인가", "링크의 원본은 어디인가", "사이드바를 어떤
순서로 채우나" 같은 질의가 위젯·다이얼로그·메뉴 곳곳에서 되풀이됐다.
:class:`Places` 가 그 묶음과 질의를 한곳에서 맡는다.

    places = Places(favorites=store, recent=recent, sidebar_urls=["~"])
    places.sidebar_urls(cwd)     # 홈 → 현재 위치 → 최근 → 북마크
    places.link_target(path)     # 링크면 원본 경로
    places.resolve_all(paths)    # 고른 경로를 원본으로 복원
"""

import os

from qtpy.QtCore import QDir, QSettings, QUrl

from .favorites import FavoritesStore
from .icons import CategoryIconProvider, clock_icon, home_icon
from .recent import RecentStore
from .util import abspath, to_urls, url_path


def as_favorites_store(value):
    """``favorites`` 인자를 :class:`FavoritesStore` (또는 None) 로 정규화한다.

    ``True`` 면 **기본 위치**(앱 데이터 폴더)에 하나 만들어 준다. 저장소는
    디스크의 폴더를 가리키는 손잡이일 뿐이라, 매번 새로 만들어도 안에 든 것은
    그대로다(``FavoritesStore()`` 200회에 4ms).
    """
    if value is True:
        return FavoritesStore()
    return value or None


def as_recent_store(value, max_items=None):
    """``recent`` 인자를 :class:`RecentStore` (또는 None) 로 정규화한다.

    ``True`` 면 기본 위치에 하나 만들고, ``max_items`` 로 기억할 개수를 정한다
    (None 이면 :data:`~custom_file_dialog.recent.DEFAULT_RECENT_MAX`).
    """
    if value is True:
        return RecentStore() if max_items is None else RecentStore(max_items=max_items)
    return value or None

# 기본으로 "사이드바에서 제거"를 막을 위치 — 사용자 홈.
DEFAULT_FIXED = ("~",)

# 기본 사이드바의 고정 자리. "현재 위치"는 다이얼로그를 열 때 뒤에 붙고,
# 최근 파일 · 북마크는 그 아래에 쌓인다.
DEFAULT_SIDEBAR = ("~",)

# 다이얼로그가 열리는 자리로 붙는 항목에 붙일 이름. 폴더 이름("workspace")보다
# 그 자리가 무엇인지 알려 주는 편이 사이드바에서 알아보기 쉽다.
CURRENT_LABEL = "현재 위치"


# Qt 가 사이드바 항목을 저장하는 위치 (QFileDialogPrivate::saveSettings 와 동일)
_SIDEBAR_SETTINGS_ORG = "QtProject"
_SIDEBAR_SETTINGS_KEY = "FileDialog/shortcuts"

# 저장된 것이 없을 때 Qt 가 쓰는 기본 사이드바.
# ``QUrl("file:")`` 는 "Computer"(파일시스템 루트) 항목이다.
def _builtin_sidebar_urls():
    return [QUrl("file:"), QUrl.fromLocalFile(QDir.homePath())]


def current_sidebar_urls():
    """지금 새 다이얼로그를 열면 보일 사이드바 항목을 QUrl 리스트로 반환한다.

    기존 항목을 그대로 두고 뒤에 몇 개만 덧붙이고 싶을 때 쓴다::

        sidebar = current_sidebar_urls() + to_urls(["~/작업", "/mnt/data"])

    Qt 는 사이드바 항목을 사용자 설정(리눅스 기준 ``~/.config/QtProject.conf``
    의 ``[FileDialog] shortcuts``)에 **영구 저장**한다. 이 함수는 그 값을 직접
    읽으므로, 돌려주는 값은 "Qt 출고 기본값"이 아니라 이전에
    :func:`exec_file_dialog` 로 지정했거나 사용자가 사이드바에 폴더를 끌어다
    놓아 저장된 현재 상태다. 저장된 것이 없으면 Qt 기본값(Computer, 홈)을
    돌려준다.

    설정을 읽기만 하므로 부작용이 없다. (QFileDialog 인스턴스를 만들어
    ``sidebarUrls()`` 를 읽는 방법은 창을 닫을 때 상태가 저장되어 사용자 설정을
    건드릴 수 있고, 위젯이 생성되기 전에는 빈 목록을 돌려주기도 한다.)
    """
    settings = QSettings(QSettings.Scope.UserScope, _SIDEBAR_SETTINGS_ORG)
    if not settings.contains(_SIDEBAR_SETTINGS_KEY):
        return _builtin_sidebar_urls()

    saved = settings.value(_SIDEBAR_SETTINGS_KEY)
    if saved is None:               # "@Invalid()" = 빈 목록으로 저장된 상태
        return []
    if not isinstance(saved, (list, tuple)):
        saved = [saved]
    return to_urls(saved)


class Places:
    """사이드바에 얹을 것들의 묶음.

    Args:
        favorites: :class:`~custom_file_dialog.favorites.FavoritesStore` (없으면 None).
        recent: :class:`~custom_file_dialog.recent.RecentStore` (없으면 None).
        sidebar_urls: 기준이 될 사이드바 목록. None 이면 기본 구성(홈 +
            다이얼로그가 열리는 현재 위치)을 쓴다.
        fixed_urls: 우클릭 "제거"를 막을 위치. None 이면 사용자 홈만 보호,
            ``[]`` 면 아무것도 보호하지 않는다.
        icon: 분류에 씌울 아이콘. ``True`` 면 즐겨찾기=별표·최근=시계·홈=집,
            ``QIcon`` 이면 즐겨찾기에 그 아이콘, ``False`` 면 홈까지 모두 Qt 기본
            폴더 아이콘.
    """

    def __init__(
        self, favorites=None, recent=None, sidebar_urls=None, fixed_urls=None, icon=True
    ):
        self.favorites = favorites
        self.recent = recent
        self.sidebar_base = sidebar_urls
        self.icon = icon
        self._fixed = {
            abspath(url_path(u) or u)
            for u in (DEFAULT_FIXED if fixed_urls is None else fixed_urls)
            if u
        }
        self._provider = None
        self._home_icon = None

    @classmethod
    def from_options(
        cls,
        favorites=None,
        recent=None,
        recent_max=None,
        sidebar_urls=None,
        fixed_urls=None,
        icon=True,
    ):
        """다이얼로그·위젯 생성자 인자를 그대로 받아 :class:`Places` 를 만든다.

        저장소 인자는 ``True`` (기본 위치에 자동 생성) · 인스턴스 · None 세 가지를
        받는다 (:func:`as_favorites_store` · :func:`as_recent_store` 와 같은 규칙).
        ``CustomFileDialog`` 와 ``FilePathEdit`` 이 같은 조립을 두 번 들고 있지
        않도록 여기 한곳에 둔다.
        """
        return cls(
            favorites=as_favorites_store(favorites),
            recent=as_recent_store(recent, recent_max),
            sidebar_urls=sidebar_urls,
            fixed_urls=fixed_urls,
            icon=icon,
        )

    def __bool__(self):
        """사이드바에 얹을 게 하나라도 있는지."""
        return bool(self.stores()) or self.sidebar_base is not None

    # ------------------------------------------------------------- 저장소
    def stores(self):
        """최근 파일 → 즐겨찾기 순서의 저장소 목록(없는 것은 빠진다)."""
        return [s for s in (self.recent, self.favorites) if s is not None]

    def favorites_store(self):
        """즐겨찾기 저장소(없으면 None)."""
        return self.favorites

    def category_store(self, path):
        """그 경로가 **분류 폴더 자체**인 저장소(아니면 None)."""
        for store in self.stores():
            if store.is_category_dir(path):
                return store
        return None

    def store_holding(self, path):
        """그 경로를 품고 있는 저장소(아니면 None)."""
        for store in self.stores():
            if store.is_inside(path):
                return store
        return None

    def is_inside(self, path):
        """즐겨찾기/최근 파일 폴더 안의 경로인지."""
        return self.store_holding(path) is not None

    def is_recent(self, store):
        """최근 파일 저장소인지(지운다기보다 '비우기'가 자연스러운 쪽)."""
        return store is not None and store is self.recent

    def store_for_category(self, category):
        """그 분류를 가진 저장소(못 찾으면 즐겨찾기)."""
        for store in self.stores():
            if os.path.isdir(store.category_dir(category)):
                return store
        return self.favorites

    # --------------------------------------------------------------- 링크
    def link_target(self, path):
        """분류 폴더 **안의** 링크면 원본 위치를 돌려준다(아니면 None).

        분류 폴더 자체와 뿌리 폴더는 진짜 폴더라 그대로 둔다.
        """
        absolute = abspath(path)
        if absolute is None:
            return None
        store = self.store_holding(absolute)
        if store is None:
            return None
        if absolute == abspath(store.base_dir) or store.is_category_dir(absolute):
            return None
        resolved = abspath(store.resolve(absolute))
        return resolved if resolved != absolute else None

    def resolve_all(self, paths):
        """고른 경로들을 원본 경로로 되돌린다(저장소 밖 경로는 그대로)."""
        for store in self.stores():
            paths = store.resolve_all(paths)
        return paths

    def record_recent(self, paths):
        """최근 파일에 기록한다(안 쓰면 아무 일도 하지 않는다)."""
        if self.recent is not None:
            self.recent.record_all(paths)

    # ------------------------------------------------------------ 사이드바
    def sidebar_urls(self, current=None):
        """다이얼로그에 넘길 최종 사이드바 목록(얹을 게 없으면 None).

        순서는 **홈 → 현재 위치 → 최근 파일 → 북마크(즐겨찾기)** 다. 고정된
        자리를 위에 두고, 계속 쌓이는 항목을 그 아래에 붙인다. 경로가 없어
        열어 볼 일이 없는 Qt 기본 "Computer" 항목은 넣지 않는다.

        Args:
            current: 다이얼로그가 열리는 폴더. ``sidebar_urls`` 로 기준 목록을
                직접 주지 않았을 때만 "현재 위치" 항목으로 붙는다(직접 준
                목록은 그대로 존중한다). 홈과 같으면 겹치지 않게 하나만 남는다.
        """
        extra = []
        for store in self.stores():
            extra += store.sidebar_urls()
        if not extra and self.sidebar_base is None:
            return None                      # 손댈 것이 없다

        if self.sidebar_base is not None:
            base = to_urls(self.sidebar_base)
        else:
            base = to_urls(DEFAULT_SIDEBAR + ((current,) if current else ()))
        return _dedup(base + extra)

    def is_fixed(self, path):
        """"사이드바에서 제거"가 막힌 위치인지(기본: 사용자 홈)."""
        absolute = abspath(path)
        return absolute is not None and absolute in self._fixed

    def fixed_urls(self):
        """제거가 막힌 위치 목록(정렬된 경로)."""
        return sorted(self._fixed)

    def sidebar_marks(self, current=None):
        """사이드바에서 **표시만** 갈아 끼울 항목 — ``{경로: (이름, 아이콘)}``.

        홈은 이름을 그대로 두고 폴더 아이콘만 **집 모양**으로 바꾸고, 다이얼로그가
        열리는 자리로 붙는 항목은 아이콘을 그대로 두고 폴더 이름 대신
        **"현재 위치"** 로 부른다. 둘 중 안 바꾸는 쪽은 ``None`` 으로 둔다.
        가리키는 경로는 그대로이므로 클릭했을 때 열리는 곳은 달라지지 않는다.

        Args:
            current: :meth:`sidebar_urls` 에 넘긴 것과 같은 "현재 위치". 홈과
                같으면 사이드바에서 한 항목으로 합쳐지므로 홈 쪽만 남긴다.
                ``sidebar_urls`` 로 기준 목록을 직접 준 경우에는 "현재 위치"
                항목을 붙이지 않았으므로 무시한다.
        """
        marks = {}
        if self.sidebar_urls(current) is None:
            return marks        # 사이드바를 우리가 채우지 않았으면 표시도 그대로

        home = abspath("~")
        if home and self.icon:
            marks[home] = (None, self.home_icon())
        if self.sidebar_base is None:
            path = abspath(current)
            if path and path != home:
                marks[path] = (CURRENT_LABEL, None)
        return marks

    # -------------------------------------------------------------- 아이콘
    def home_icon(self):
        """홈 항목에 씌울 집 아이콘 (처음 쓸 때 그린다)."""
        if self._home_icon is None:
            self._home_icon = home_icon()
        return self._home_icon

    def icon_provider(self):
        """분류에 아이콘을 씌우는 제공자(쓰지 않으면 None).

        QPixmap 을 그리려면 QApplication 이 필요하므로 처음 쓸 때 만든다.
        """
        if not self.icon or not self.stores():
            return None
        if self._provider is None:
            provider = CategoryIconProvider()
            if self.favorites is not None:
                # icon 에 QIcon 을 주면 즐겨찾기에 별표 대신 그 아이콘을 쓴다
                provider.add_store(
                    self.favorites, None if self.icon is True else self.icon
                )
            if self.recent is not None:
                provider.add_store(self.recent, clock_icon())
            self._provider = provider
        return self._provider


def _dedup(urls):
    """같은 위치가 두 번 들어가지 않게 한다(홈에서 열면 "현재 위치"와 겹친다)."""
    seen, result = set(), []
    for url in urls:
        key = abspath(url_path(url)) or url.toString()
        if key not in seen:
            seen.add(key)
            result.append(url)
    return result
