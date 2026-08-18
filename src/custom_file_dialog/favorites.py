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

from . import safety
from .util import abspath, to_urls

# 분류 폴더들이 들어갈 기본 위치의 하위 폴더 이름
DEFAULT_DIRNAME = "favorites"

# 저장소 뿌리의 기본 폴더 이름 (~/.config/<이 이름>/favorites · recent)
DEFAULT_STORAGE_DIRNAME = "custom_file_dialog"

# configure_favorites() 로 지정하는 즐겨찾기 폴더 위치 (None 이면 뿌리 아래)
_CONFIGURED_BASE_DIR = None

# configure_storage() 로 지정하는 저장소 뿌리 (None 이면 ~/.config 아래)
_CONFIGURED_STORAGE_DIR = None

# 링크 -> 원본 매핑을 적어 두는 파일.
# 심볼릭 링크는 realpath 로 원본을 되찾을 수 있지만, 윈도우 폴백인 하드링크는
# 그럴 수 없어(하드링크는 원본과 동등한 경로다) 매핑을 따로 남겨 둔다.
INDEX_FILENAME = ".index.json"

# 파일 이름 하나가 가질 수 있는 최대 바이트 수(리눅스 등 대부분 NAME_MAX=255).
# **이 한도로만 거절한다.** 더 낮춰 잡으면, 예전 판으로 이미 만들어 둔 분류
# 폴더(200~255바이트)를 **조회조차 못 하게** 된다 — _safe_name 은 만들 때만이
# 아니라 category_dir 을 거치는 모든 조회(사이드바·메뉴·items)가 지나는
# 길목이라, 그 자리에서 예외가 나면 우클릭 한 번에 앱이 죽는다.
_MAX_NAME_BYTES = 255

# 자동으로 따온 표시 이름을 줄일 때 쓰는 한도. 중복이면 " (2)" 가 붙고 저장 중
# 임시 이름이 될 수도 있어 위 한도보다 여유를 둔다.
_LINK_NAME_BYTES = 240


def _looks_like_path(text):
    """이름이 아니라 **경로**로 준 것인지 — 구분자가 있거나 ``~`` 로 시작한다."""
    return (
        os.sep in text
        or "/" in text
        or text.startswith("~")
        or os.path.isabs(text)
    )


def _safe_link_name(name):
    """표시 이름을 링크 파일 이름으로 쓸 수 있게 다듬는다.

    :func:`_safe_name` 과 같은 손질을 하되 **예외를 던지지 않는다.** 분류
    이름은 사용자가 직접 짓는 것이라 잘못되면 알려 줘야 하지만, 표시 이름은
    고른 파일에서 자동으로 따오기도 한다 — 리눅스에서 합법인 이름(공백뿐인
    이름 등)에 예외가 나면 "즐겨찾기에 추가"가 엉뚱한 경고를 띄우고, 최근
    기록은 그 지점에서 **뒤 파일들까지 통째로** 빠진다.
    """
    try:
        return _safe_name(_shorten(str(name), _LINK_NAME_BYTES))
    except ValueError:
        return "항목"


def _shorten(text, limit=None):
    """이름을 파일시스템이 받는 바이트 수 안으로 줄인다 (확장자는 살린다).

    자르지 않으면 폴더/링크를 만드는 순간 ``OSError(ENAMETOOLONG)`` 가 난다.
    확장자를 살리는 것은 필터·연결 프로그램이 그것을 보기 때문이다.
    """
    if limit is None:
        limit = _MAX_NAME_BYTES
    if len(text.encode("utf-8", "surrogateescape")) <= limit:
        return text
    stem, ext = os.path.splitext(text)
    ext_bytes = ext.encode("utf-8", "surrogateescape")
    if len(ext_bytes) > limit // 2:         # 확장자랄 것이 아니다
        stem, ext_bytes = text, b""
        ext = ""
    room = limit - len(ext_bytes)
    # 잘린 자리에 걸친 글자는 버린다(반쪽 바이트가 남지 않게)
    stem = stem.encode("utf-8", "surrogateescape")[:room].decode("utf-8", "ignore")
    return (stem or "항목") + ext


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


def configure_storage(base_dir):
    """즐겨찾기·최근 파일 저장소가 들어갈 **뿌리 폴더**를 지정한다.

    앱 시작 시 한 번 부르면 기본 생성 위치가 통째로 옮겨진다::

        configure_storage("~/.config/myapp")
        FavoritesStore()        # -> ~/.config/myapp/favorites
        RecentStore()           # -> ~/.config/myapp/recent

    ``~`` 는 홈으로 펼쳐진다. ``None`` 을 주면 기본 위치
    (``~/.config/custom_file_dialog`` — ``XDG_CONFIG_HOME`` 을 따른다)로
    되돌린다. 즐겨찾기 폴더 **하나만** 다른 곳에 두려면
    :func:`configure_favorites` 를 쓴다(이쪽이 우선한다).

    Returns:
        적용된 뿌리 폴더.
    """
    global _CONFIGURED_STORAGE_DIR
    _CONFIGURED_STORAGE_DIR = abspath(base_dir) if base_dir else None
    return default_storage_dir()


def default_storage_dir():
    """즐겨찾기·최근 파일 폴더들이 들어갈 뿌리 폴더.

    :func:`configure_storage` 로 지정한 위치, 없으면
    ``~/.config/custom_file_dialog`` (정확히는 ``XDG_CONFIG_HOME`` 을 따르는
    ``QStandardPaths.GenericConfigLocation`` 아래)다.
    """
    if _CONFIGURED_STORAGE_DIR:
        return _CONFIGURED_STORAGE_DIR
    root = QStandardPaths.writableLocation(_standard_location("GenericConfigLocation"))
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(root, DEFAULT_STORAGE_DIRNAME)


def default_base_dir():
    """즐겨찾기 폴더의 기본 위치.

    다음 순서로 정해진다.

    1. :func:`configure_favorites` 로 지정한 위치
    2. :func:`configure_storage` 로 지정한 뿌리 아래의 ``favorites``
    3. ``~/.config/custom_file_dialog/favorites`` (``XDG_CONFIG_HOME`` 준수)

    개별 저장소만 다른 곳에 두려면 ``FavoritesStore(base_dir=...)`` 를 쓴다.
    """
    if _CONFIGURED_BASE_DIR:
        return _CONFIGURED_BASE_DIR
    return os.path.join(default_storage_dir(), DEFAULT_DIRNAME)


def _safe_name(name):
    """분류 이름을 폴더 이름으로 쓸 수 있게 다듬는다.

    경로 구분자는 어디서나 막고, 윈도우에서는 파일명에 못 쓰는 문자
    (``: * ? " < > |``)도 바꾼다. 리눅스에서는 그 문자들이 합법이라 기존
    분류 폴더 이름이 달라지지 않도록 건드리지 않는다.
    """
    text = str(name).strip()
    if not text:
        raise ValueError("분류 이름이 비어 있습니다.")
    # 널 바이트와 지나치게 긴 이름은 여기서 막는다. 그냥 통과시키면 폴더를
    # 만드는 순간 ValueError("embedded null byte") · OSError(ENAMETOOLONG) 가
    # 저수준에서 튀어나와, 이 함수가 약속한 "다듬거나 ValueError" 밖의 예외를
    # 호출자가 받는다.
    if "\0" in text:
        raise ValueError("분류 이름에 널 바이트를 쓸 수 없습니다.")
    if len(text.encode("utf-8", "surrogateescape")) > _MAX_NAME_BYTES:
        raise ValueError("분류 이름이 너무 깁니다 (%d바이트 이하): %r"
                         % (_MAX_NAME_BYTES, text[:20] + "…"))
    bad_chars = ["/", "\\", os.sep]
    if os.name == "nt":
        bad_chars += [":", "*", "?", '"', "<", ">", "|"]
    for bad in bad_chars:
        if bad:
            text = text.replace(bad, "_")
    if text in (".", ".."):
        raise ValueError("사용할 수 없는 분류 이름입니다: %r" % name)
    return text


def _scan_names(directory):
    """그 폴더 **안의 이름**들(이름순). 없으면 빈 목록."""
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _index_stamp(path):
    """인덱스 파일의 (수정시각, 크기). 없으면 None."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _scan_dirs(directory):
    """그 폴더 **안의 폴더 이름**들(이름순). 없으면 빈 목록.

    ``listdir`` 뒤에 이름마다 ``isdir`` 을 부르지 않는다 — 이름 수만큼 stat 이
    붙고, 네트워크 저장소에서는 그 하나하나가 서버 왕복이다. ``scandir`` 은
    대개 디렉터리 항목에 들어 있는 종류를 그대로 쓴다.

    **한 번의 호출 안에서 다 읽는다.** 반쯤 게으른 이터레이터를 돌려주면
    바깥에서 도는 동안 일어나는 I/O 가 타임아웃 밖으로 새어 나간다.
    """
    try:
        with os.scandir(directory) as entries:
            return sorted(entry.name for entry in entries if entry.is_dir())
    except OSError:
        return []


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
        self._index_cache = None        # (파일 mtime_ns, 크기, dict) — _load_index 참고
        if create:
            os.makedirs(self.base_dir, exist_ok=True)

    # ------------------------------------------------------------- 분류
    def categories(self):
        """분류 이름 목록(이름순).

        **저장소 폴더를 읽는 것도 안전장치를 거친다.** 이 저장소의 기본 자리는
        ``~/.config`` — 이 라이브러리가 상정하는 **네트워크 홈** 위다. 그런데
        여기를 맨 ``os.listdir`` 로 읽고 있어서, 홈이 멈추면 다이얼로그를 여는
        것만으로 GUI 가 그대로 잡혔다(실측: ``path_timeout=1.0`` 을 주고도
        다이얼로그 생성에 9.04초). 이 라이브러리가 막겠다는 바로 그 사고를
        정작 제 저장소에서 냈다.

        멈추면 **빈 목록**으로 물러선다 — 사이드바에 분류가 안 보일 뿐 창은
        뜬다. 이 패키지가 다른 곳에서 지키는 규칙과 같다.
        """
        return safety.safe_call(_scan_dirs, self.base_dir, [])

    def category_dir(self, category):
        """분류 폴더의 경로(없어도 경로만 계산해서 반환)."""
        return os.path.join(self.base_dir, _safe_name(category))

    def is_category_dir(self, path, is_dir=None):
        """주어진 경로가 이 저장소의 분류 폴더인지 여부.

        아이콘을 씌우거나 메뉴를 띄울 대상을 고를 때 쓴다. 아이콘 제공자가
        **파일 목록의 항목마다** 부르므로, 문자열 비교(부모가 base_dir 인가)로
        먼저 거른 뒤에만 파일시스템을 만진다. 저장소 밖 경로 — 대부분의 경우 —
        는 시스템 콜 없이 끝나고, 죽은 마운트의 항목을 그리다 멈추는 일도 없다.

        Args:
            is_dir: 폴더인지 **이미 아는** 호출자가 그 답을 넘기면 여기서
                다시 확인하지 않는다. 아이콘 제공자가 그런 경우다 — Qt 가
                항목을 그리려고 이미 stat 해서 채워 둔 ``QFileInfo`` 를 들고
                우리를 부른다. 같은 stat 을 두 번 하면 네트워크 저장소에서
                그 왕복이 그대로 목록 지연이 된다.
        """
        absolute = abspath(path)
        if absolute is None:
            return False
        if os.path.dirname(absolute) != self._normal_base():
            return False
        # **안전장치를 거친다.** 이 함수는 목록의 항목마다 불리는데(아이콘
        # 제공자), 저장소가 멈추면 항목 하나에 그대로 매달렸다(실측 6.00초).
        return safety.safe_isdir(absolute) if is_dir is None else bool(is_dir)

    def _normal_base(self):
        """정규화한 ``base_dir`` (항목마다 다시 계산하지 않게 기억해 둔다)."""
        cached = getattr(self, "_normal_base_cache", None)
        if cached is None or cached[0] != self.base_dir:
            cached = (self.base_dir, os.path.normpath(self.base_dir))
            self._normal_base_cache = cached
        return cached[1]

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
        try:
            os.rmdir(directory)
        except OSError:
            # 링크 하나가 안 지워졌으면(권한 등) 폴더가 남는다. 우클릭 슬롯까지
            # 예외가 튀어 오르는 것보다, 남은 것을 두고 인덱스만 맞추는 편이 낫다.
            pass
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
        # 저장소 안(분류 폴더 · 그 안의 링크 · 뿌리)은 등록하지 않는다. 링크
        # 창고를 가리키는 링크가 생겨, 들어가면 원본이 아니라 창고를 헤매게
        # 된다(분류 안의 분류처럼 순환도 만들어진다).
        if self._is_inside(target):
            raise FavoritesError("즐겨찾기 폴더 안의 항목은 등록할 수 없습니다: %s" % path)
        # 등록 대상은 사용자가 고른 경로라 죽은 원격 마운트일 수 있다. 평범한
        # exists 는 그런 자리에서 돌아오지 않아 GUI 가 통째로 멈춘다(D 상태).
        if not safety.safe_exists(target):
            # safe_exists 의 False 는 "없다"가 아니라 **"확인하지 않았다"**
            # 일 수도 있다(차단 경로 · automount 위 · 죽은 원격). 있는 폴더를
            # "존재하지 않습니다"라고 하면 사용자는 오타를 찾으러 간다.
            if not safety.is_reachable(target):
                raise FavoritesError(
                    "지금은 확인할 수 없는 자리라 등록하지 않습니다"
                    " (폴더를 먼저 연 뒤 다시 시도하세요): %s" % path
                )
            raise FavoritesError("등록할 대상이 존재하지 않습니다: %s" % path)

        directory = self.add_category(category)

        index = self._load_index()
        existing = self.link_for(category, target, _index=index)
        if existing is not None:
            return existing

        # 표시 이름도 분류 이름과 같은 손질을 거친다. 그러지 않으면
        # name="../../x" 같은 값이 그대로 경로가 되어 **저장소 밖에** 링크가
        # 생긴다(그렇게 만들어진 것은 복원도 제거도 되지 않는다).
        link_name = _safe_link_name(name or os.path.basename(target.rstrip(os.sep)))
        link_path = self._available_link_path(directory, link_name)
        self._make_link(target, link_path)

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

        text = str(path_or_name)
        index = self._load_index()
        entries = [
            (name, os.path.join(directory, name))
            for name in sorted(os.listdir(directory))
        ]

        # **이름이 먼저다.** 상대 경로를 곧바로 펴면 abspath 가 현재 작업
        # 디렉터리를 기준으로 삼아, 우연히 같은 곳을 가리키는 다른 링크까지
        # 지울 수 있다. 이름으로 찾지 못했을 때만 경로로 해석한다(add 는 상대
        # 경로를 받아 주므로 remove 도 받아야 대칭이다).
        matched = [link for name, link in entries if name == text]
        if not matched and _looks_like_path(text):
            # **경로처럼 생겼을 때만** 편다. 그냥 이름을 폈다가는 현재 작업
            # 디렉터리 기준으로 해석되어, 이름이 안 맞는데 우연히 그곳을
            # 가리키는 다른 항목이 지워진다(링크 이름에는 구분자가 못 들어간다).
            wanted = abspath(text)
            matched = [
                link
                for _name, link in entries
                if self._target_of(link, index) == wanted
            ]

        removed = False
        for link in matched:
            index.pop(os.path.normpath(link), None)
            self._unlink(link)
            removed = True

        if removed:
            self._save_index(index)
        return removed

    def entries(self, category):
        """``[(표시이름, 원본경로)]`` 목록(이름순).

        이름을 읽는 것도 안전장치를 거친다 — 멈춘 저장소에서는 빈 목록이다
        (실측: 안 거쳤을 때 27.00초).
        """
        directory = self.category_dir(category)
        names = safety.safe_call(_scan_names, directory, [])
        if not names:
            return []
        index = self._load_index()
        return [
            (name, self._target_of(os.path.join(directory, name), index))
            for name in names
        ]

    def items(self, category):
        """분류에 등록된 **원본** 경로 목록."""
        return [target for _name, target in self.entries(category)]

    def contains(self, category, path):
        """해당 대상이 이미 등록되어 있는지."""
        return self.link_for(category, path) is not None

    def link_for(self, category, path, _index=None):
        """대상에 해당하는 링크 경로(없으면 None).

        ``_index`` 로 이미 읽어 둔 인덱스를 재사용할 수 있다(내부 최적화용).
        """
        target = abspath(path)
        directory = self.category_dir(category)
        names = safety.safe_call(_scan_names, directory, [])
        if not names:
            return None
        index = self._load_index() if _index is None else _index
        for name in names:
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
        """여러 경로를 한 번에 되돌린다.

        인덱스 파일은 **처음 저장소 안 경로를 만났을 때 한 번만** 읽는다.
        여러 개 선택(수십~수백 경로)마다 경로 수만큼 다시 읽지 않게 한다.
        """
        index = None
        out = []
        for path in paths or []:
            absolute = abspath(path) if path else None
            if absolute is None or not self._is_inside(absolute):
                out.append(path)
                continue
            if index is None:
                index = self._load_index()
            out.append(self._target_of(absolute, index))
        return out

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
        """링크가 가리키는 원본 경로. 인덱스 -> 링크 내용 순으로 찾는다.

        ``os.path.realpath`` 를 쓰지 않는다 — 그 함수는 **대상을 따라가며
        stat** 하므로, 원본이 죽은 원격 마운트에 있으면 목록을 그리는 것만으로
        GUI 가 멈춘다. ``readlink`` 는 링크 자신의 내용만 읽어 절대 멈추지
        않는다(우리가 만드는 링크는 절대 경로 한 단계다).
        """
        # **링크 자신이 먼저다.** 인덱스는 하드링크·정션(윈도우)처럼 링크로는
        # 원본을 되찾을 수 없는 경우를 위한 보조 수단인데, 우클릭 "이름 변경"
        # 으로 링크 이름이 바뀌면 낡은 매핑이 그대로 남는다. 그것을 먼저 믿으면
        # 다른 항목의 원본을 돌려주고, 저장 모드에서는 엉뚱한 파일을 덮어쓴다.
        # 링크를 푸는 일 전체를 **한 번의 안전한 호출**로 감싼다. 안쪽은
        # readlink 뿐이라 원본이 죽어 있어도 안 멈추지만, **저장소 자신이**
        # 멈추면 그 readlink 에서 매달린다 — 파일을 고른 직후 자동으로 도는
        # 길이라 그대로 GUI 지연이 됐다(실측 18.00초). 못 풀면 인덱스로 간다.
        followed = safety.safe_call(self._follow_or_none, link, None)
        if followed is not None:
            return followed
        recorded = index.get(os.path.normpath(link))
        if recorded:
            return recorded
        return safety.safe_call(self._follow_links, link, link)

    def _follow_or_none(self, link):
        """링크면 원본을, 링크가 아니면 ``None``.

        :meth:`_target_of` 가 "링크 우선, 없으면 인덱스" 순서를 지키려면
        **링크가 아니었다**를 구분해야 한다.
        """
        if not os.path.islink(link):
            return None
        return self._follow_links(link)

    def _follow_links(self, path):
        """저장소 안 경로에서 **링크만** 풀어 원본 경로를 만든다.

        분류 폴더의 링크 자체(``<분류>/산출물``)뿐 아니라 그 **안쪽**
        (``<분류>/산출물/안쪽``)도 풀어야 한다. 그래서 조상을 거슬러 올라가며
        심볼릭 링크인 자리를 찾아 그 내용으로 갈아 끼우고, 나머지는 문자열로
        이어 붙인다. 읽는 것은 링크 자신뿐이라(``islink`` · ``readlink``)
        원본이 죽은 마운트에 있어도 멈추지 않는다.
        """
        absolute = os.path.normpath(path)
        base = os.path.normpath(self.base_dir)
        current, rest = absolute, []
        while current.startswith(base):
            try:
                if os.path.islink(current):
                    target = os.readlink(current)
                    if not os.path.isabs(target):
                        target = os.path.join(os.path.dirname(current), target)
                    return os.path.normpath(os.path.join(target, *rest))
            except OSError:
                break
            parent = os.path.dirname(current)
            if parent == current or current == base:
                break               # 저장소 뿌리까지 왔는데 링크가 없다
            rest.insert(0, os.path.basename(current))
            current = parent
        return absolute             # 링크가 아니면(하드링크·정션) 그 자체가 대상

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
        is_dir = safety.safe_isdir(target)
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
        # islink 를 **먼저** 본다. isdir 는 링크를 따라가며 대상을 stat 하므로,
        # 원본이 죽은 원격 마운트인 링크를 지우려는 순간 GUI 가 멈춘다.
        # islink 는 링크 자신만 보므로(lstat) 절대 멈추지 않는다.
        try:
            if not os.path.islink(link) and os.path.isdir(link):
                os.rmdir(link)      # 윈도우 정션
            else:
                os.remove(link)
        except OSError:
            pass

    # 인덱스 파일 (링크 -> 원본)
    def _index_path(self):
        return os.path.join(self.base_dir, INDEX_FILENAME)

    # 비UTF-8 파일명(옛 공유 폴더의 EUC-KR 등)은 파이썬에 서러게이트 문자열로
    # 들어온다. 순정 utf-8 로는 인코딩이 안 돼 저장이 터지므로, 원본 바이트를
    # 그대로 오가는 surrogateescape 로 읽고 쓴다(경로가 바이트 단위로 보존된다).
    _INDEX_ERRORS = "surrogateescape"

    def _load_index(self):
        """인덱스를 읽는다. 파일이 안 바뀌었으면 캐시 사본을 돌려준다.

        이 함수는 선택이 바뀔 때마다(링크 -> 원본 복원) 불리는데, 저장소가
        네트워크 홈에 있으면 그때마다 파일을 다시 읽는 비용이 그대로 지연이
        된다. (mtime, 크기)가 같으면 디스크를 열지 않는다. 다른 프로세스가
        고친 것은 mtime 변화로 알아챈다. 돌려주는 dict 은 늘 **사본**이다 —
        호출자들이 고쳐서 다시 저장하는 패턴이라 캐시 원본을 주면 안 된다.
        """
        path = self._index_path()
        stamp = safety.safe_call(_index_stamp, path, None)
        if stamp is None:
            self._index_cache = None
            return {}
        cached = self._index_cache
        if cached is not None and cached[:2] == stamp:
            return dict(cached[2])
        try:
            with open(path, encoding="utf-8", errors=self._INDEX_ERRORS) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            self._index_cache = None
            return {}
        if not isinstance(data, dict):
            data = {}
        self._index_cache = stamp + (dict(data),)
        return data

    def _save_index(self, index):
        """인덱스를 **통째로 바꿔 끼운다** (임시 파일에 쓰고 이름을 바꾼다).

        제자리에서 잘라 쓰면 저장 도중 끊겼을 때(종료 · 디스크 가득 · 네트워크
        홈의 순간적 오류) 깨진 JSON 이 남는다. 읽기는 그것을 조용히 ``{}`` 로
        보고, **다음 저장이 그 위에 한 항목만 얹어** 나머지 매핑을 영구히
        지운다. 심볼릭 링크는 링크에서 원본을 되찾을 수 있어 티가 안 나지만,
        하드링크·정션은 복구 수단이 없다.
        """
        path = self._index_path()
        temporary = path + ".tmp"
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(
                temporary, "w", encoding="utf-8", errors=self._INDEX_ERRORS
            ) as handle:
                json.dump(index, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            stat = os.stat(path)
            self._index_cache = (stat.st_mtime_ns, stat.st_size, dict(index))
        except (OSError, UnicodeError):
            # 인덱스는 보조 수단이라 실패해도 치명적이지 않다. 캐시만 비워서
            # 다음 읽기가 디스크 상태를 그대로 따르게 한다.
            self._index_cache = None
            try:
                os.remove(temporary)
            except OSError:
                pass


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
