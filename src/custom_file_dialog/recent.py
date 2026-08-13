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
from .favorites import FavoritesStore, _safe_name, default_storage_dir
from .util import abspath

# 사이드바에 표시될 이름(= 분류 폴더 이름)
DEFAULT_RECENT_NAME = "최근 파일"

# 기억할 최근 파일 개수
DEFAULT_RECENT_MAX = 20

# 즐겨찾기 폴더 옆에 만들 최근 파일 폴더 이름
DEFAULT_RECENT_DIRNAME = "recent"


def default_recent_dir():
    """최근 파일 폴더의 기본 위치 — 저장소 뿌리 아래의 ``recent``.

    예전에는 "즐겨찾기 폴더의 **부모** 아래"로 계산했다. 그러면
    :func:`~custom_file_dialog.favorites.configure_favorites` 로 즐겨찾기만
    옮겨 놨을 때 그 부모가 홈이 되어, 사용자 홈에 ``recent/`` 가 덜렁
    만들어졌다(``configure_storage`` 의 계약과도 어긋난다).
    """
    return os.path.join(default_storage_dir(), DEFAULT_RECENT_DIRNAME)


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
            self.add_category(self.name)

    # --------------------------------------------------------------- 기록
    def record(self, path):
        """최근 목록 맨 앞에 기록한다. 만들어진 링크 경로(또는 None).

        - 파일만 기록한다(폴더는 무시).
        - 이미 있으면 지웠다 다시 만들어 맨 앞으로 올린다.
        - 최근 파일/즐겨찾기 폴더 안의 링크 자체는 기록하지 않는다
          (원본 경로로 바꿔서 넘겨야 한다).
        """
        if self.max_items <= 0 or not path:
            return None

        target = abspath(path)
        # 고른 파일이 죽은 원격 마운트일 수 있다. 평범한 isfile 은 그런 자리에서
        # 돌아오지 않아, 확정 직후의 기록만으로 GUI 가 멈춘다(D 상태).
        if not safety.safe_isfile(target) or self.is_inside(target):
            return None

        # 있으면 지우고 다시 만들어 최신으로 끌어올린다. 없을 때의 remove 는
        # 목록 한 번 훑고 끝이라, 미리 있는지 조회하는 것보다 싸다.
        self.remove(self.name, target)

        link = self.add(self.name, target)
        self._evict()
        return link

    def record_all(self, paths):
        """여러 경로를 순서대로 기록한다(뒤에 준 것이 더 최근이 된다)."""
        return [link for link in (self.record(p) for p in (paths or [])) if link]

    def set_max_items(self, max_items):
        self.max_items = max(0, int(max_items))
        self._evict()

    # --------------------------------------------------------------- 조회
    def links(self, category=None):
        """최신순 링크 경로 목록."""
        directory = self.category_dir(category or self.name)
        if not os.path.isdir(directory):
            return []
        links = [os.path.join(directory, n) for n in os.listdir(directory)]
        # 링크 자신의 수정 시각 = 만든 시각. 최신이 앞으로 오게 내림차순.
        links.sort(key=self._link_mtime, reverse=True)
        return links

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
    def _link_mtime(self, link):
        """링크 자신의 수정 시각(대상이 아니라). 없으면 0."""
        try:
            return os.lstat(link).st_mtime
        except OSError:
            return 0.0

    def _evict(self):
        """개수 제한을 넘긴 오래된 항목을 지운다."""
        links = self.links()
        if len(links) <= self.max_items:
            return
        index = self._load_index()
        for link in links[self.max_items :]:
            index.pop(os.path.normpath(link), None)
            self._unlink(link)
        self._save_index(index)
