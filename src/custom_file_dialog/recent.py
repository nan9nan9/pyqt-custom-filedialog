"""최근 파일: 최근에 고른 파일을 사이드바 항목 하나로 모아 둔다.

즐겨찾기와 완전히 같은 방식(분류 폴더 + 심볼릭 링크)을 쓰되, 분류가
``최근 파일`` 하나뿐이고 **개수 제한과 최신순 정렬을 스스로 관리**한다는 점만
다르다. 그래서 :class:`~custom_file_dialog.favorites.FavoritesStore` 를 그대로
물려받아 사이드바 등록·경로 복원·정리 메뉴를 모두 재사용한다.

순서는 **심볼릭 링크 자신의 수정 시각**으로 판단한다(링크를 만든 시점).
이미 있는 파일을 다시 고르면 링크를 지웠다 새로 만들어 맨 앞으로 끌어올린다.
별도 상태 파일이 필요 없다.
"""

import os

from . import safety
from .favorites import (
    FavoritesError,
    FavoritesStore,
    _ensure_dir,
    _safe_name,
    default_storage_dir,
)
from .util import abspath

# 사이드바에 표시될 이름(= 분류 폴더 이름)
DEFAULT_RECENT_NAME = "최근 파일"

# 기억할 최근 파일 개수
DEFAULT_RECENT_MAX = 20

# 즐겨찾기 폴더 옆에 만들 최근 파일 폴더 이름
DEFAULT_RECENT_DIRNAME = "recent"


def default_recent_dir():
    """최근 파일 폴더의 기본 위치 — 저장소 뿌리 아래의 ``recent``."""
    return os.path.join(default_storage_dir(), DEFAULT_RECENT_DIRNAME)


def _scan_links(directory):
    """링크 경로들을 **최신순**으로(만든 시각 = 링크 자신의 수정 시각).

    목록과 시각 읽기를 **한 번의 호출 안에서** 끝낸다 — 나눠 놓으면 바깥에서
    도는 동안 일어나는 I/O 가 타임아웃 밖으로 새어 나간다. ``scandir`` 의
    항목은 그 자리에서 stat 값을 들고 있어 왕복도 덜 든다.
    """
    try:
        with os.scandir(directory) as entries:
            found = [
                (entry.path, entry.stat(follow_symlinks=False).st_mtime)
                for entry in entries
            ]
    except OSError:
        return []
    found.sort(key=lambda item: item[1], reverse=True)
    return [path for path, _mtime in found]


def _count_entries(directory):
    """항목 수만. 정렬하지 않으므로 항목마다의 stat 이 붙지 않는다."""
    try:
        return len(os.listdir(directory))
    except OSError:
        return 0


class RecentStore(FavoritesStore):
    """최근에 고른 파일을 자동으로 모아 두는 저장소.

    사용 예::

        recent = RecentStore(max_items=20)
        recent.record("/proj/a/설계도.csv")     # 고를 때마다 기록
        recent.items()                          # 최신순 원본 경로 목록

        edit = FilePathEdit(mode="open_file", recent_files=recent)
        # -> 사이드바에 "최근 파일" 이 추가되고, 클릭하면 그 목록이 나온다

    Args:
        base_dir: 폴더를 둘 위치. None 이면 :func:`default_recent_dir`.
        name: 사이드바에 표시할 이름(= 분류 폴더 이름).
        max_items: 기억할 개수. 넘치면 오래된 것부터 지운다.
        create: 생성 시 폴더를 미리 만들지 여부.
        link_mode: :class:`FavoritesStore` 와 동일.
    """

    def __init__(
        self,
        base_dir=None,
        name=DEFAULT_RECENT_NAME,
        max_items=DEFAULT_RECENT_MAX,
        create=True,
        link_mode="auto",
    ):
        super().__init__(
            base_dir=base_dir or default_recent_dir(),
            create=create,
            link_mode=link_mode,
        )
        self.name = _safe_name(name)
        self.max_items = max(0, int(max_items))
        if create and self.max_items > 0:
            # 뿌리와 같은 이유로 **여기도 감싼다** — 이 줄 역시 다이얼로그를
            # 열기 전에 도는 자리다(:meth:`FavoritesStore._ensure_base_dir`).
            # add_category 를 그대로 부르면 멈춘 홈에서 그만큼 또 매달린다.
            safety.safe_call(_ensure_dir, self.category_dir(self.name), False)

    # --------------------------------------------------------------- 기록
    def record(self, path):
        """최근 목록 맨 앞에 기록한다. 만들어진 링크 경로(또는 None).

        - 파일만 기록한다(폴더는 무시).
        - 이미 있으면 지웠다 다시 만들어 맨 앞으로 올린다.
        - **이 저장소 안**의 링크는 기록하지 않는다.

        다만 **즐겨찾기 링크는 걸러 내지 못한다** — 이 클래스는 자기 폴더만
        알기 때문이다. 그대로 주면 :meth:`items` 가 원본 대신 링크 창고
        경로(``<favorites>/설계/a.csv``)를 돌려주고, 즐겨찾기에서 그 항목을
        빼는 순간 최근 목록의 그 항목이 끊긴 링크가 된다. 저장소가 여럿인 앱은
        :meth:`custom_file_dialog.places.Places.record_recent` 를 써라 —
        거기서 모든 저장소의 링크를 원본으로 풀어서 넘긴다.
        """
        if self.max_items <= 0 or not path:
            return None

        # **저장소 자체가 닿는 자리인지 먼저 본다.** 이 함수는 파일을 고른
        # **직후** 자동으로 불리고, 저장소의 기본 자리는 네트워크 홈이다.
        # 안쪽에서 하는 일이 여럿이라(있던 링크 찾기 · 링크 만들기 · 인덱스
        # 쓰기 · 넘친 것 자르기) 저마다 멈추면 그 합만큼 GUI 가 잡힌다
        # (실측: 멈춘 저장소에서 18.04초. 확인 한 번을 앞에 두니 0.3초).
        # 닿지 않으면 기록을 **거른다** — 고른 결과는 그대로 나간다.
        if not safety.is_reachable(self.base_dir):
            return None

        target = abspath(path)
        # 고른 파일이 죽은 원격 마운트일 수 있다. 평범한 isfile 은 그런 자리에서
        # 돌아오지 않아, 확정 직후의 기록만으로 GUI 가 멈춘다(D 상태).
        if not safety.safe_isfile(target) or self.is_inside(target):
            return None

        # 손대기 **전** 개수를 세어 둔다 — 아래 _evict 가 남의 기록을 자르지
        # 않으려면 이 값이 필요하다. remove 뒤에 세면 이미 있던 파일을 다시
        # 고를 때 한 칸씩 줄어든다(지웠다 다시 넣는 방식이라 길이가 같다).
        stored = self._count()

        # 있으면 지우고 다시 만들어 최신으로 끌어올린다. 없을 때의 remove 는
        # 목록 한 번 훑고 끝이라, 미리 있는지 조회하는 것보다 싸다.
        self.remove(self.name, target)

        link = self.add(self.name, target)
        self._evict(keep=stored)
        return link

    def record_all(self, paths):
        """여러 경로를 순서대로 기록한다(뒤에 준 것이 더 최근이 된다).

        하나가 실패해도 **나머지는 계속 기록한다.** 여러 개를 고르는 자리에서
        중간의 한 파일 때문에 뒤엣것들이 통째로 빠지면 안 된다.
        """
        links = []
        for path in paths or []:
            try:
                link = self.record(path)
            except (OSError, ValueError, FavoritesError):
                continue
            if link:
                links.append(link)
        return links

    def set_max_items(self, max_items):
        self.max_items = max(0, int(max_items))
        self._evict()

    # --------------------------------------------------------------- 조회
    def links(self, category=None):
        """최신순 링크 경로 목록.

        **저장소 폴더를 읽는 것도 안전장치를 거친다.** 이 저장소의 기본 자리는
        ``~/.config`` — 이 라이브러리가 상정하는 네트워크 홈 위이고, 이 함수는
        파일을 고른 **직후**에도 불린다(:meth:`record` 안의 정리). 맨 ``listdir``
        + 링크마다 ``lstat`` 이라, 홈이 멈추면 그 자리에서 GUI 가 잡혔다.
        멈추면 빈 목록으로 물러선다.
        """
        directory = self.category_dir(category or self.name)
        return safety.safe_call(_scan_links, directory, [])

    def entries(self, category=None):
        """``[(표시이름, 원본경로)]`` 최신순."""
        index = self._load_index()
        return [
            (os.path.basename(link), self._target_of(link, index))
            for link in self.links(category)
        ]

    def items(self, category=None):
        """최근에 고른 **원본** 경로 목록(최신순)."""
        return [target for _name, target in self.entries(category)]

    def clear(self, category=None):
        """목록만 비운다(사이드바 항목은 남긴다)."""
        index = self._load_index()
        for link in self.links(category):
            index.pop(os.path.normpath(link), None)
            self._unlink(link)
        self._save_index(index)

    # --------------------------------------------------------------- 내부
    def _count(self):
        """분류 폴더의 항목 수. **정렬하지 않으므로 lstat 을 하지 않는다.**

        개수만 필요한 자리에서 :meth:`links` 를 부르면 최신순으로 세우려고
        링크마다 lstat 을 건다. 이 저장소의 기본 위치는 ``~/.config`` — 이
        라이브러리가 상정하는 **네트워크 홈** 위이고, 부르는 곳이 "확정 직후"
        라 그 왕복이 그대로 GUI 지연이 된다(실측: 목록 100개일 때 lstat
        100회가 더 붙었다).
        """
        return safety.safe_call(_count_entries, self.category_dir(self.name), 0)

    def _link_mtime(self, link):
        """링크 자신의 수정 시각(대상이 아니라). 없으면 0."""
        try:
            return os.lstat(link).st_mtime
        except OSError:
            return 0.0

    def _evict(self, keep=0):
        """개수 제한을 넘긴 오래된 항목을 지운다.

        Args:
            keep: **이미 들어 있던** 개수. 내 몫보다 많이 있으면 그만큼은
                남긴다 — 같은 폴더를 개수가 다른 곳이 함께 쓸 수 있기 때문이다.
                ``recent_files=True`` 로 자동 생성하면 모두 같은 기본 위치를
                가리키므로, 30개짜리가 쌓아 둔 목록을 20개짜리가 한 번 기록하는
                순간 링크 10개가 **디스크에서 지워졌다**(4개 바인딩 전부 재현).
                :meth:`custom_file_dialog.history.PathHistory.add` 와
                :meth:`custom_file_dialog.places.PlacesOptions.update` 가 이미
                같은 규칙을 지키는데 정작 자르는 이 자리에만 빠져 있었다.

                :meth:`set_max_items` 처럼 **의도적으로 줄이는** 호출은 이 값을
                주지 않는다(0) — 그때는 정말 줄이는 것이 맞다.
        """
        limit = max(self.max_items, keep)
        links = self.links()
        if len(links) <= limit:
            return
        index = self._load_index()
        for link in links[limit:]:
            index.pop(os.path.normpath(link), None)
            self._unlink(link)
        self._save_index(index)
