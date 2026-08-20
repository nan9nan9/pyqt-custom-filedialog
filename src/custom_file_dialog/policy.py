"""``/user`` 처럼 **건드리면 안 되는 자리**를 정하고, 동작마다 판정한다.

automount 아래에서는 폴더를 읽는 것도, 이름 하나를 stat 하는 것도 마운트를
부른다. 그래서 **동작이 무엇을 만지게 되는지**로 판정을 나눈다. 두 가지만
알면 나머지는 따라온다.

- :func:`risky_place` — 그 자리를 **통째로 읽으면** 위험한가
  (지목된 자리 · 얕은 자리 · autofs 위)
- :func:`risky_name` — 그 **이름 하나를 stat 하면** 위험한가
  (= 그 **부모**가 위험한가. ``/user/my`` 의 stat 은 ``/user`` 안에서 ``my`` 를
  찾는 일이다)

네 판정은 그 둘에 "누가 시켰는가"를 곱한 것이다. 사용자가 명시적으로 시킨
쪽이 한 단계 관대하다 — 목록에 이미 보이는 항목을 누르거나, 끝에 구분자를
붙여 "이 폴더를 열겠다"고 밝히거나, 마운트 키보다 깊은 경로를 끝까지 쳐서
"붙일 것은 이 하나"라고 밝힌 경우다.

======================  ========================  ========================
동작이 만지는 것          자동 (우리가 건다)         사용자 명시 (눌렀다)
======================  ========================  ========================
폴더를 통째로            :func:`may_list`          :func:`may_enter`
                        (+ ``allow_listing``)
경로 이름 하나           :func:`may_stat`          :func:`may_open`
                                                  (+ 끝 구분자 · 마운트 키보다
                                                  깊은 경로 예외)
======================  ========================  ========================

위험하다고 판정되면 **디스크를 아예 만지지 않는다** — 도달성 확인
(:mod:`~custom_file_dialog.reach`)조차 하지 않는다. 문자열과 마운트 표만 보므로
이 모듈의 판정은 절대 멈추지 않는다.

설정은 :func:`~custom_file_dialog.safety.configure` 로 한다(그 겉면이 이 모듈과
:mod:`~custom_file_dialog.reach` 에 나눠 넣는다).
"""

import os
import threading

from . import mounts
from .mounts import abspath as _abspath

# 자동완성이 폴더를 나열할 최소 깊이. 0 이면 제한 없음(어디서든 완성한다).
DEFAULT_MIN_DEPTH = 0

# 자동완성이 폴더 목록을 읽어도 되는지. False 면 깊이와 무관하게 하나도 안 읽는다.
DEFAULT_ALLOW_LISTING = True

_lock = threading.Lock()
_settings = {
    "guarded_roots": [],
    "min_depth": DEFAULT_MIN_DEPTH,
    "allow_listing": DEFAULT_ALLOW_LISTING,
}


def configure(guarded_roots=None, min_depth=None, allow_listing=None):
    """정책 설정을 바꾼다 (인자 설명은 :func:`custom_file_dialog.safety.configure`)."""
    with _lock:
        if guarded_roots is not None:
            if isinstance(guarded_roots, (str, bytes, os.PathLike)):
                # 흔한 실수: configure(guarded_roots="/user"). 그대로 순회하면
                # 한 글자씩 쪼개져 막으려던 자리는 안 막히고 루트가 차단된다.
                guarded_roots = [guarded_roots]
            roots = []
            for root in guarded_roots:
                if isinstance(root, bytes):
                    root = os.fsdecode(root)    # 빈 값 판정도 편 뒤에 한다
                if str(root).strip():
                    roots.append(_abspath(root))
            _settings["guarded_roots"] = roots
        if min_depth is not None:
            _settings["min_depth"] = max(0, int(min_depth))
        if allow_listing is not None:
            _settings["allow_listing"] = bool(allow_listing)


def settings():
    """현재 정책 설정 사본."""
    with _lock:
        return dict(_settings, guarded_roots=list(_settings["guarded_roots"]))


def reset():
    """정책 설정을 기본값으로 되돌린다."""
    with _lock:
        _settings.update(
            guarded_roots=[],
            min_depth=DEFAULT_MIN_DEPTH,
            allow_listing=DEFAULT_ALLOW_LISTING,
        )


def guarded_roots():
    """:func:`configure` 로 지정한, 그 자체는 열지 않을 경로 목록."""
    with _lock:
        return list(_settings["guarded_roots"])


def is_guarded(path):
    """그 경로 **자체**가 열지 말아야 할 자리인지.

    ``guarded_roots`` 에 ``/user`` 를 넣었다면 ``/user`` 는 True, ``/user/myaccount``
    같은 하위 경로는 False 다(하위는 평소대로 쓴다).
    """
    if not path:
        return False
    absolute = _abspath(path)
    with _lock:
        roots = _settings["guarded_roots"]
    return absolute in roots


def min_depth():
    """:func:`configure` 로 지정한 자동완성 최소 깊이 (0 이면 제한 없음)."""
    with _lock:
        return _settings["min_depth"]


def path_depth(path):
    """루트에서부터 센 경로 단계 수.

    ``/`` 는 0, ``/user`` 는 1, ``/user/myaccount`` 는 2. 상대 경로와 ``~`` 는 먼저
    절대 경로로 편 뒤에 센다. 문자열만 보므로 파일시스템을 건드리지 않는다.

    윈도우에서는 드라이브도 한 단계로 센다(``C:\\작업`` 은 2). automount 는
    리눅스 기능이라 ``min_depth`` 를 윈도우에서 쓸 일은 드물지만, 쓴다면 이
    차이를 감안해 값을 정한다.
    """
    if not path:
        return 0
    absolute = _abspath(path)
    return len([part for part in absolute.split(os.sep) if part])


def is_too_shallow(path):
    """자동완성이 **나열하지 않을** 만큼 얕은 자리인지.

    ``min_depth`` 를 지정하지 않았으면 언제나 False 다.
    """
    limit = min_depth()
    return limit > 0 and path_depth(path) < limit


def listing_allowed():
    """자동완성이 폴더 목록을 읽어도 되는지 (``allow_listing`` 설정)."""
    with _lock:
        return _settings["allow_listing"]


def risky_place(path):
    """그 자리를 **통째로 읽으면** 위험한가 — 나열·진입 판정의 공통 뼈대.

    폴더를 읽으면 Qt 가 항목마다 stat 하므로, automount 아래에서는 그것이 곧
    "그 폴더의 모든 이름을 마운트"다. 셋 중 하나면 위험하다.

    - ``guarded_roots`` 로 지목한 자리
    - ``min_depth`` 보다 얕은 자리
    - autofs 마운트 위 (설정 없이 마운트 표에서 자동으로 알아본다)
    """
    return is_guarded(path) or is_too_shallow(path) or mounts.on_automount(path)


def risky_name(path):
    """그 **이름 하나를 stat 하면** 위험한가 — 자동 확인 판정의 뼈대.

    경로 하나를 stat 하는 것은 "부모 폴더 안에서 그 이름을 찾는" 일이라
    **부모**가 기준이다(``/user/my`` 를 stat = ``/user`` 안에서 ``my`` 를 찾음
    = automounter 에게 ``my`` 를 마운트해 보라는 뜻). 경로 자신이 autofs 위인
    것도 함께 본다 — 아직 안 붙은 이름일 수 있다.
    """
    return risky_place(os.path.dirname(_abspath(path))) or mounts.on_automount(path)


def may_list(path):
    """그 폴더를 **자동완성이** 읽어도 되는지.

    :func:`risky_place` 에 더해 ``allow_listing`` 스위치를 본다 — 그 설정은
    자동완성 전용이라 여기서만 걸린다(사용자가 눌러 들어가는 :func:`may_enter`
    는 보지 않는다).
    """
    return listing_allowed() and not risky_place(path)


def protection_active():
    """지금 다이얼로그에 안전장치를 걸어야 하는 상태인지.

    ``guarded_roots`` · ``min_depth`` 중 하나라도 켜졌거나, ``allow_listing``
    을 껐거나, 시스템에 autofs 마운트가 있으면 True. 이 상태에서는 **OS
    네이티브 다이얼로그를 쓸 수 없다** — 그 창은 OS 가 그려서 자동완성도
    확정도 가로챌 수 없기 때문이다
    (:func:`~custom_file_dialog.dialog.exec_file_dialog` 가 Qt 자체 창으로
    자동 전환한다).
    """
    return bool(
        guarded_roots() or min_depth() or not listing_allowed() or mounts.has_automounts()
    )


def blocks_navigation():
    """**이동·확정까지** 막아야 하는 상태인지.

    ``guarded_roots`` · ``min_depth`` 중 하나라도 켜졌거나 시스템에 autofs 가
    있으면 True. ``allow_listing`` 은 보지 않는다 — 자동완성을 끄는 스위치이지
    사용자가 눌러 들어가는 것까지 막는 뜻이 아니다
    (:func:`protection_active` 는 그것까지 세므로 더 넓다).

    무엇을 걸지 정하는 판단을 :mod:`~custom_file_dialog.guard` 가 설정에서
    직접 읽어 조합하고 있었다. 그러면 규칙이 두 곳으로 갈라져, 한쪽만 고치는
    실수가 난다 — 판단은 여기 한곳에 둔다.
    """
    return bool(guarded_roots() or min_depth() or mounts.has_automounts())


def may_stat(path):
    """입력 중인 경로를 **자동으로** 만져(stat) 봐도 되는지.

    타이핑 도중의 미완성 경로를 키 입력마다 stat 하면 글자마다 마운트 시도가
    일어난다(``/user/my`` → ``my`` 를 마운트해 보라는 뜻). 위험하면 **디스크를
    아예 만지지 않는다** — 스레드+타임아웃 확인조차 하지 않는다.

    자동 확인(자동완성 · 키 입력마다의 유효성 표시)에만 쓴다. 사용자가 Enter 나
    버튼으로 **확정**하는 쪽은 :func:`may_open` 이 한 단계 관대하게 판정한다.
    """
    return not path or not risky_name(path)


def may_enter(path):
    """그 자리를 **열어(=들어가) 되는지**.

    들어가면 그 폴더가 통째로 나열되므로 :func:`risky_place` 를 그대로 쓴다.
    ``allow_listing`` 은 보지 않는다 — 그 설정은 자동완성을 끄는 스위치이지,
    사용자가 눌러서 들어가는 것까지 막는 뜻이 아니다.
    """
    return not risky_place(path)


def may_open(path):
    """그 경로를 **확정**(Enter · 열기 버튼)해도 되는지 — 명시적이라 한 단계 관대하다.

    확정도 결국 그 이름 하나를 stat 하는 일이라 :func:`risky_name` 을 본다.
    자동 확인보다 관대해지는 자리는 둘뿐이고, 둘 다 "그 stat 이 부르는 마운트가
    **하나뿐**임을 사용자가 명시했다"는 같은 이유다.

    1. **끝에 구분자를 붙인 폴더 표기** — "이 폴더를 열겠다"는 명시. 깊이만
       충분하면(``min_depth`` 이상) 위험한 자리라도 연다.
    2. **마운트 키보다 깊은 경로** — ``/user`` 가 autofs 일 때
       ``/user/myaccount/proj/a.csv`` 를 확정하면 붙는 마운트는
       ``/user/myaccount`` 하나뿐이다(:func:`~custom_file_dialog.mounts.automount_key`).
       ``/user/myaccount/`` 를 여는 것과 정확히 같은 일이라 같은 기준으로 연다.

    ``min_depth=2`` · ``/user`` 가 autofs 인 경우::

        /user/my            Enter -> 막힘  (오타일 수 있다. 부모가 위험한 자리)
        /user/myaccount     Enter -> 막힘  (마운트 키 자체 — 구분자로 밝혀야 한다)
        /user/myaccount/    Enter -> 열림  (명시적 폴더 표기 + 깊이 충족)
        /user/myaccount/a.csv Enter -> 열림 (키보다 깊다. 붙는 건 myaccount 하나)

    2번이 없으면 **경로를 끝까지 친 파일을 고를 수 없다** — 아직 안 붙은
    automount 아래에서는 전체 경로가 그대로 막히고 "끝에 '/' 를 붙이세요" 만
    나온다(파일에는 따를 수 없는 안내다). 게다가 그 자리가 붙어 있는 동안에는
    멀쩡히 열리므로, 같은 경로가 되다 안 되다 하는 것으로 보인다.

    지목해서 막은 자리(``guarded_roots``) **자체**는 구분자를 붙여도 열지 않고,
    그 **바로 아래**도 구분자 없이는 열지 않는다(2번 예외도 여기엔 안 준다 —
    앱이 이름으로 지목한 자리라 마운트 개수와 무관하게 막아야 한다).
    """
    if not path:
        return True
    text = str(path).strip()
    explicit_dir = text.endswith(("/", os.sep))
    absolute = _abspath(text)
    if is_guarded(absolute):
        return False                    # 지목한 자리 자체는 언제나 안 된다

    limit = min_depth()
    if explicit_dir:
        # "이 폴더를 열겠다"는 명시. 그래도 **automount 지점 자체**는 안 된다 —
        # 그 자리를 여는 것은 아래 이름을 전부 마운트해 보라는 뜻이라, 하나만
        # 붙이는 하위(``/user/myaccount/``)와 위험이 다르다.
        if mounts.is_automount_point(absolute):
            return False
        return limit <= 0 or path_depth(absolute) >= limit

    if not risky_name(absolute):
        return True

    # 위험하다고 봤지만, **automount 키보다 깊은** 경로는 예외다(docstring 2번).
    # 그 확정의 stat 이 부르는 마운트는 키 하나뿐이라, 사용자가 명시적으로 여는
    # ``키/`` 와 위험이 같다. 풀어 주는 것은 **autofs 라는 이유 하나뿐**이라,
    # 나머지 두 이유(지목된 부모 · 얕은 부모)는 평소의 구분자 없는 확정과
    # 똑같이 부모를 기준으로 그대로 본다.
    parent = os.path.dirname(absolute)
    if is_guarded(parent) or is_too_shallow(parent):
        return False
    key = mounts.automount_key(absolute)
    return bool(key) and key != absolute
