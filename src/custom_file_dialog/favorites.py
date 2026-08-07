"""즐겨찾기: 파일/폴더를 분류별로 모아 QFileDialog 사이드바에 띄운다.

QFileDialog 사이드바는 **디렉터리만** 받는다(파일 URL 을 넣으면 Qt 가 조용히
버린다). 그래서 분류마다 실제 디렉터리를 하나 만들고 그 안에 대상들의
**심볼릭 링크**를 모아 둔 뒤, 그 디렉터리를 사이드바에 등록한다. 사이드바에서
분류를 클릭하면 오른쪽 목록에 등록해 둔 파일·폴더가 함께 나온다::

    <base_dir>/
        설계/
            설계도.csv  ->  /proj/a/설계도.csv
            산출물      ->  /proj/b/산출물
        보고서/
            보고서.md   ->  /proj/b/보고서.md

디렉터리 구조 자체가 저장소라 별도 영속화가 필요 없다. 다만 사용자가 링크를
고르면 링크 경로가 반환되므로, :meth:`FavoritesStore.resolve` 로 원래 경로를
복원해야 한다(:class:`~custom_file_dialog.path_edit.FilePathEdit` 는 자동으로 한다).
"""

import json
import os
import subprocess

from qtpy.QtCore import QStandardPaths

from .util import abspath, to_urls

# 분류 폴더들이 들어갈 기본 위치의 하위 폴더 이름
DEFAULT_DIRNAME = "favorites"

# configure_favorites() 로 지정하는 앱 전체 기본 위치 (None 이면 OS 표준 위치)
_CONFIGURED_BASE_DIR = None

# 링크 -> 원본 매핑을 적어 두는 파일.
# 심볼릭 링크는 realpath 로 원본을 되찾을 수 있지만, 윈도우 폴백인 하드링크는
# 그럴 수 없어(하드링크는 원본과 동등한 경로다) 매핑을 따로 남겨 둔다.
INDEX_FILENAME = ".index.json"


class FavoritesError(RuntimeError):
    """즐겨찾기 링크를 만들지 못했을 때 발생한다."""


def _standard_location(name):
    """QStandardPaths 값을 바인딩에 무관하게 얻는다."""
    scope = getattr(QStandardPaths, "StandardLocation", QStandardPaths)
    return getattr(scope, name)


def configure_favorites(base_dir):
    """``base_dir`` 없이 만든 :class:`FavoritesStore` 가 쓸 기본 위치를 지정한다.

    앱 시작 시 한 번 부르면 이후 ``FavoritesStore()`` 가 모두 이 위치를 쓴다::

        configure_favorites("/opt/myapp/즐겨찾기")
        store = FavoritesStore()            # -> /opt/myapp/즐겨찾기

    ``~`` 는 홈 디렉터리로 펼쳐진다. ``None`` 을 주면 지정을 취소하고 OS 표준
    위치(:func:`default_base_dir` 참고)로 돌아간다.

    Returns:
        적용된 기본 위치(취소했으면 OS 표준 위치).
    """
    global _CONFIGURED_BASE_DIR
    if base_dir:
        _CONFIGURED_BASE_DIR = abspath(base_dir)
    else:
        _CONFIGURED_BASE_DIR = None
    return default_base_dir()


def configured_base_dir():
    """:func:`configure_favorites` 로 지정한 위치(지정 안 했으면 None)."""
    return _CONFIGURED_BASE_DIR


def default_base_dir():
    """즐겨찾기 폴더의 기본 위치.

    다음 순서로 정해진다.

    1. :func:`configure_favorites` 로 지정한 위치
    2. ``QStandardPaths.AppDataLocation`` 아래의 ``favorites``
    3. 앱 이름이 없어 2번을 못 얻으면 ``~/.custom_file_dialog/favorites``

    개별 저장소만 다른 곳에 두려면 ``FavoritesStore(base_dir=...)`` 를 쓴다.
    """
    if _CONFIGURED_BASE_DIR:
        return _CONFIGURED_BASE_DIR
    root = QStandardPaths.writableLocation(_standard_location("AppDataLocation"))
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".custom_file_dialog")
    return os.path.join(root, DEFAULT_DIRNAME)


def _safe_name(name):
    """분류 이름을 폴더 이름으로 쓸 수 있게 다듬는다."""
    text = str(name).strip()
    if not text:
        raise ValueError("분류 이름이 비어 있습니다.")
    for bad in ("/", "\\", os.sep):
        if bad:
            text = text.replace(bad, "_")
    if text in (".", ".."):
        raise ValueError("사용할 수 없는 분류 이름입니다: %r" % name)
    return text


class FavoritesStore:
    """즐겨찾기 분류를 심볼릭 링크 폴더로 관리한다.

    사용 예::

        store = FavoritesStore()                  # 기본 위치에 생성
        store.add("설계", "/proj/a/설계도.csv")
        store.add("설계", "/proj/b/산출물")        # 폴더도 가능
        store.add("보고서", "/proj/b/보고서.md")

        edit = FilePathEdit(mode="open_file", favorites=store)
        # -> 사이드바에 "설계", "보고서" 가 추가되고,
        #    클릭하면 오른쪽에 등록한 항목들이 나온다

    Args:
        base_dir: 분류 폴더들을 둘 위치. None 이면 :func:`default_base_dir`
            (= :func:`configure_favorites` 로 지정한 위치, 없으면 OS 표준 위치).
            ``~`` 표기를 쓸 수 있다.
        create: 생성 시 폴더를 미리 만들지 여부.
        link_mode: ``"auto"`` 는 심볼릭 링크를 먼저 시도하고 실패하면
            하드링크(파일) / 정션(폴더)으로 폴백한다. ``"symlink"`` 는 폴백 없이
            심볼릭 링크만 쓴다(실패 시 :class:`FavoritesError`).
    """

    def __init__(self, base_dir=None, create=True, link_mode="auto"):
        if link_mode not in ("auto", "symlink"):
            raise ValueError("link_mode 는 'auto' 또는 'symlink' 여야 합니다.")
        self.base_dir = abspath(base_dir) or abspath(default_base_dir())
        self._link_mode = link_mode
        if create:
            os.makedirs(self.base_dir, exist_ok=True)

    # ------------------------------------------------------------- 분류
    def categories(self):
        """분류 이름 목록(이름순)."""
        if not os.path.isdir(self.base_dir):
            return []
        return sorted(
            name
            for name in os.listdir(self.base_dir)
            if os.path.isdir(os.path.join(self.base_dir, name))
        )

    def category_dir(self, category):
        """분류 폴더의 경로(없어도 경로만 계산해서 반환)."""
        return os.path.join(self.base_dir, _safe_name(category))

    def is_category_dir(self, path):
        """주어진 경로가 이 저장소의 분류 폴더인지 여부.

        아이콘을 씌우거나 메뉴를 띄울 대상을 고를 때 쓴다. 아이콘 제공자가
        **파일 목록의 항목마다** 부르므로, 문자열 비교(부모가 base_dir 인가)로
        먼저 거른 뒤에만 파일시스템을 만진다. 저장소 밖 경로 — 대부분의 경우 —
        는 시스템 콜 없이 끝나고, 죽은 마운트의 항목을 그리다 멈추는 일도 없다.
        """
        absolute = abspath(path)
        if absolute is None:
            return False
        if os.path.dirname(absolute) != os.path.normpath(self.base_dir):
            return False
        return os.path.isdir(absolute)

    def add_category(self, category):
        """분류 폴더를 만들고 경로를 반환한다(이미 있으면 그대로)."""
        directory = self.category_dir(category)
        os.makedirs(directory, exist_ok=True)
        return directory

    def remove_category(self, category):
        """분류를 통째로 지운다(원본 파일은 건드리지 않는다)."""
        directory = self.category_dir(category)
        if not os.path.isdir(directory):
            return
        index = self._load_index()
        for name in os.listdir(directory):
            link = os.path.join(directory, name)
            index.pop(os.path.normpath(link), None)
            self._unlink(link)
        os.rmdir(directory)
        self._save_index(index)

    # ------------------------------------------------------------- 항목
    def add(self, category, path, name=None):
        """분류에 파일 또는 폴더를 등록하고 만들어진 링크 경로를 반환한다.

        Args:
            category: 분류 이름(사이드바에 이 이름으로 표시된다).
            path: 등록할 파일/폴더의 실제 경로.
            name: 목록에 보일 이름. None 이면 원본 파일명을 쓴다.

        이미 같은 대상이 같은 분류에 있으면 새로 만들지 않고 기존 링크 경로를
        돌려준다. 이름만 겹치고 대상이 다르면 ``이름 (2)`` 처럼 번호를 붙인다.
        """
        target = abspath(path)
        if not os.path.exists(target):
            raise FavoritesError("등록할 대상이 존재하지 않습니다: %s" % path)

        directory = self.add_category(category)

        existing = self.link_for(category, target)
        if existing is not None:
            return existing

        link_name = name or os.path.basename(target.rstrip(os.sep)) or "항목"
        link_path = self._available_link_path(directory, link_name)
        self._make_link(target, link_path)

        index = self._load_index()
        index[os.path.normpath(link_path)] = target
        self._save_index(index)
        return link_path

    def remove(self, category, path_or_name):
        """등록된 항목을 뺀다(원본 파일은 건드리지 않는다).

        원본 경로로도, 목록에 보이는 이름으로도 지울 수 있다.
        """
        directory = self.category_dir(category)
        if not os.path.isdir(directory):
            return False

        wanted = abspath(path_or_name)
        index = self._load_index()
        removed = False

        for name in list(os.listdir(directory)):
            link = os.path.join(directory, name)
            if name == str(path_or_name) or self._target_of(link, index) == wanted:
                index.pop(os.path.normpath(link), None)
                self._unlink(link)
                removed = True

        if removed:
            self._save_index(index)
        return removed

    def entries(self, category):
        """``[(표시이름, 원본경로)]`` 목록(이름순)."""
        directory = self.category_dir(category)
        if not os.path.isdir(directory):
            return []
        index = self._load_index()
        return [
            (name, self._target_of(os.path.join(directory, name), index))
            for name in sorted(os.listdir(directory))
        ]

    def items(self, category):
        """분류에 등록된 **원본** 경로 목록."""
        return [target for _name, target in self.entries(category)]

    def contains(self, category, path):
        """해당 대상이 이미 등록되어 있는지."""
        return self.link_for(category, path) is not None

    def link_for(self, category, path):
        """대상에 해당하는 링크 경로(없으면 None)."""
        target = abspath(path)
        directory = self.category_dir(category)
        if not os.path.isdir(directory):
            return None
        index = self._load_index()
        for name in sorted(os.listdir(directory)):
            link = os.path.join(directory, name)
            if self._target_of(link, index) == target:
                return link
        return None

    def clear(self, category=None):
        """분류 하나(또는 전체)를 비운다."""
        for name in [category] if category is not None else self.categories():
            self.remove_category(name)

    # ---------------------------------------------------------- 사이드바
    def sidebar_urls(self):
        """분류 폴더들을 사이드바에 넣을 QUrl 리스트로 반환한다."""
        return to_urls([self.category_dir(name) for name in self.categories()])

    def resolve(self, path):
        """즐겨찾기 링크 경로를 원본 경로로 되돌린다.

        즐겨찾기 폴더 밖의 경로는 그대로 돌려주므로, 사용자가 고른 경로를
        일괄로 통과시켜도 안전하다.
        """
        if not path:
            return path
        absolute = abspath(path)
        if not self._is_inside(absolute):
            return path
        return self._target_of(absolute, self._load_index())

    def resolve_all(self, paths):
        return [self.resolve(p) for p in (paths or [])]

    def is_inside(self, path):
        """주어진 경로가 즐겨찾기 폴더 안에 있는지 여부."""
        if not path:
            return False
        return self._is_inside(abspath(path))

    def _is_inside(self, absolute):
        try:
            return os.path.commonpath([absolute, self.base_dir]) == self.base_dir
        except ValueError:      # 윈도우에서 드라이브가 다르면
            return False

    # ------------------------------------------------------------- 내부
    def _target_of(self, link, index):
        """링크가 가리키는 원본 경로. 인덱스 -> realpath 순으로 찾는다."""
        recorded = index.get(os.path.normpath(link))
        if recorded:
            return recorded
        return os.path.realpath(link)

    def _available_link_path(self, directory, link_name):
        """이름이 겹치면 ``이름 (2)`` 처럼 번호를 붙여 빈 경로를 찾는다."""
        candidate = os.path.join(directory, link_name)
        if not os.path.lexists(candidate):
            return candidate
        root, ext = os.path.splitext(link_name)
        for number in range(2, 1000):
            candidate = os.path.join(directory, "%s (%d)%s" % (root, number, ext))
            if not os.path.lexists(candidate):
                return candidate
        raise FavoritesError("이름이 너무 많이 겹칩니다: %s" % link_name)

    def _make_link(self, target, link_path):
        is_dir = os.path.isdir(target)
        try:
            os.symlink(target, link_path, target_is_directory=is_dir)
            return
        except (OSError, NotImplementedError, AttributeError) as exc:
            if self._link_mode == "symlink":
                raise FavoritesError(
                    "심볼릭 링크를 만들지 못했습니다: %s (%s)" % (link_path, exc)
                )
            symlink_error = exc

        # 폴백: 폴더는 정션(윈도우), 파일은 하드링크
        if is_dir:
            if os.name == "nt" and _make_junction(target, link_path):
                return
        else:
            try:
                os.link(target, link_path)
                return
            except OSError:
                pass

        raise FavoritesError(
            "즐겨찾기 링크를 만들지 못했습니다: %s -> %s (%s)\n"
            "윈도우라면 개발자 모드를 켜거나 관리자 권한이 필요할 수 있습니다."
            % (link_path, target, symlink_error)
        )

    def _unlink(self, link):
        try:
            if os.path.isdir(link) and not os.path.islink(link):
                os.rmdir(link)      # 윈도우 정션
            else:
                os.remove(link)
        except OSError:
            pass

    # 인덱스 파일 (링크 -> 원본)
    def _index_path(self):
        return os.path.join(self.base_dir, INDEX_FILENAME)

    def _load_index(self):
        try:
            with open(self._index_path(), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_index(self, index):
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self._index_path(), "w", encoding="utf-8") as handle:
                json.dump(index, handle, ensure_ascii=False, indent=1)
        except OSError:
            pass        # 인덱스는 보조 수단이라 실패해도 치명적이지 않다


def _make_junction(target, link_path):
    """윈도우 디렉터리 정션 생성 (성공하면 True)."""
    try:
        subprocess.check_call(
            ["cmd", "/c", "mklink", "/J", link_path, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False
