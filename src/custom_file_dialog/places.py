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

from .favorites import FavoritesError, FavoritesStore
from .icons import CategoryIconProvider, clock_icon, home_icon, star_icon
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
        # 저장소 인자는 여기서도 정규화한다 — ``True`` 를 그대로 들고 있으면
        # stores() 에 bool 이 섞여 첫 질의에서 AttributeError 로 터진다.
        # (:class:`PlacesOptions` 를 거치지 않고 직접 만드는 경우가 있다.)
        self.favorites = as_favorites_store(favorites)
        self.recent = as_recent_store(recent)
        self.sidebar_base = sidebar_urls
        self.icon = icon
        self._fixed = {
            abspath(url_path(u) or u)
            for u in (DEFAULT_FIXED if fixed_urls is None else fixed_urls)
            if u
        }
        self._provider = None
        self._home_icon = None
        self._category_icons = {}       # id(저장소) -> 아이콘 (처음 쓸 때 그린다)

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
        """생성자 인자를 그대로 받아 :class:`Places` 를 만든다(예전 이름).

        규칙은 :class:`PlacesOptions` 하나에 모여 있다 — 이 이름은 그쪽으로
        넘겨 주기만 한다. **인자 이름과 순서는 예전 그대로**여서 위치 인자로
        부르던 코드도 계속 동작한다.
        """
        return PlacesOptions(
            favorites=favorites,
            recent=recent,
            recent_max=recent_max,
            sidebar_urls=sidebar_urls,
            fixed_urls=fixed_urls,
            icon=icon,
        ).places()

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
        """최근 파일에 기록한다(안 쓰면 아무 일도 하지 않는다).

        **실패해도 조용히 넘어간다.** 이 함수는 사용자가 파일을 고른 **뒤**
        따라오는 부수 효과이고, 부르는 쪽은 둘 다 Qt 슬롯이다(다이얼로그의
        ``accepted`` · 위젯의 찾아보기 버튼). 슬롯에서 예외가 새어 나가면
        PyQt 는 앱을 abort 시킨다 — 링크를 못 만들었다고 선택 자체가 무산되면
        안 된다. 실패하는 경우: 심볼릭 링크가 안 되는 자리(윈도우 비개발자
        모드 · FAT32 · 일부 CIFS)에 저장소가 있고 하드링크 폴백도 볼륨이 달라
        실패할 때, 홈이 읽기 전용이거나 가득 찼을 때.
        """
        if self.recent is None:
            return
        try:
            self.recent.record_all(paths)
        except (OSError, ValueError, FavoritesError):
            pass

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

        # 분류(★ 즐겨찾기 · 🕘 최근 파일)도 여기서 씌운다. 아이콘 제공자에
        # 맡기면 QUrlModel 이 파일시스템 통지를 받을 때마다 경로에서 아이콘을
        # 다시 읽어 폴더 아이콘으로 되돌아간다(모듈 설명 참고).
        for store in self.stores():
            icon = self.category_icon(store)
            if icon is None:
                continue
            for url in store.sidebar_urls():
                path = abspath(url_path(url))
                if path:
                    marks[path] = (None, icon)
        return marks

    def category_icon(self, store):
        """그 저장소의 분류에 씌울 아이콘 (``icon=False`` 면 None).

        즐겨찾기는 별표(또는 ``icon`` 으로 준 QIcon), 최근 파일은 시계.
        ``QPixmap`` 을 그려야 하므로 처음 쓸 때 만들어 저장소별로 캐시한다.
        """
        if not self.icon or store is None:
            return None
        key = id(store)
        if key not in self._category_icons:
            if store is self.recent:
                self._category_icons[key] = clock_icon()
            elif self.icon is not True:
                self._category_icons[key] = self.icon    # 직접 준 QIcon
            else:
                self._category_icons[key] = star_icon()
        return self._category_icons[key]

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


class PlacesOptions:
    """사이드바 구성 **설정**을 들고 있다가 :class:`Places` 를 만들어 준다.

    위젯(:class:`~custom_file_dialog.path_edit.FilePathEdit`)은 이 설정들을
    실행 중에 바꿀 수 있어야 해서, "설정 여섯 개를 들고 있다가 하나라도 바뀌면
    다음에 쓸 때 다시 만든다"는 관리를 하고 있었다. 그 상태와 규칙을 한곳에
    모은다 — 위젯은 값을 넘기고 :meth:`places` 만 부르면 된다.

        options = PlacesOptions(favorites=store, recent_files=True)
        options.places().sidebar_urls(cwd)
        options.update(favorites=None)      # 다음 places() 는 새로 만든다

    저장소 인자는 :class:`Places` 와 같은 규칙을 따른다 — ``True`` 면 기본
    위치에 만들고, 인스턴스를 주면 그대로 쓰고, ``False``/``None`` 이면 안 쓴다.
    """

    _KEYS = ("favorites", "recent", "sidebar_urls", "fixed_urls", "icon")

    def __init__(
        self,
        favorites=None,
        recent=None,
        recent_max=None,
        sidebar_urls=None,
        fixed_urls=None,
        icon=True,
    ):
        self.favorites = as_favorites_store(favorites)
        self.recent = as_recent_store(recent, recent_max)
        self.sidebar_urls = list(sidebar_urls) if sidebar_urls is not None else None
        self.fixed_urls = list(fixed_urls) if fixed_urls is not None else None
        self.icon = icon
        self._cache = None

    def places(self):
        """지금 설정으로 만든 :class:`Places` (설정이 바뀌면 다시 만든다).

        ``Places`` 는 아이콘 제공자 참조도 들고 있어(Qt 가 소유하지 않는다)
        같은 것을 계속 쓰는 편이 낫다. 그래서 캐시한다.
        """
        if self._cache is None:
            self._cache = Places(
                favorites=self.favorites,
                recent=self.recent,
                sidebar_urls=self.sidebar_urls,
                fixed_urls=self.fixed_urls,
                icon=self.icon,
            )
        return self._cache

    def update(self, **changes):
        """설정을 바꾸고 캐시를 버린다 (``recent_max`` 는 ``recent`` 와 함께 준다)."""
        recent_max = changes.pop("recent_max", None)
        # 하나라도 모르는 이름이면 **아무것도 바꾸지 않는다.** 절반만 반영된 채
        # 예외가 나가면 캐시된 Places 와 설정이 어긋난 채로 남는다.
        unknown = sorted(set(changes) - set(self._KEYS))
        if unknown:
            raise TypeError("모르는 설정입니다: %s" % ", ".join(repr(k) for k in unknown))

        if "favorites" in changes:
            changes["favorites"] = as_favorites_store(changes["favorites"])
        if "recent" in changes:
            changes["recent"] = as_recent_store(changes["recent"], recent_max)
        for key, value in changes.items():
            if key in ("sidebar_urls", "fixed_urls") and value is not None:
                value = list(value)
            setattr(self, key, value)
        self._cache = None


def _dedup(urls):
    """같은 위치가 두 번 들어가지 않게 한다(홈에서 열면 "현재 위치"와 겹친다)."""
    seen, result = set(), []
    for url in urls:
        key = abspath(url_path(url)) or url.toString()
        if key not in seen:
            seen.add(key)
            result.append(url)
    return result
