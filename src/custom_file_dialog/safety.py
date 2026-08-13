"""느리거나 죽은 네트워크 경로에서 GUI 가 멈추지 않도록 미리 걸러 낸다.

이 모듈은 **세 층을 하나의 겉면으로 묶는다.** 앱은 여기만 쓰면 되고, 안을
들여다볼 일이 있으면 각 층의 설명이 훨씬 자세하다.

===============================================  ============================
:mod:`~custom_file_dialog.mounts`                마운트 표만 읽어 경로의
                                                 성격을 알려 준다 (파일시스템
                                                 미접근)
:mod:`~custom_file_dialog.policy`                건드리면 안 되는 자리를 정하고
                                                 동작마다 판정한다
:mod:`~custom_file_dialog.reach`                 죽은 원격 마운트에서 멈추지
                                                 않게 확인한다
===============================================  ============================

아래로 갈수록 위 층에 기댄다(``reach`` → ``policy`` → ``mounts``). 층을 나눈
이유는 **"파일시스템을 언제 만지는가"** 가 층마다 다르기 때문이다 — ``mounts``
와 ``policy`` 는 문자열과 마운트 표만 보므로 절대 멈추지 않고, 실제로 만지는
것은 ``reach`` 뿐이다. 그래서 정책이 "위험"이라고 하면 ``reach`` 까지 가지도
않는다.

    from custom_file_dialog import safety

    safety.configure(timeout=1.0, guarded_roots=["/user"], min_depth=2)
    if safety.is_reachable("/nfs/proj/a.csv"):
        ...

.. note::

   여기 있는 이름들은 각 층의 함수를 **가리키는 참조**다(겉면이므로). 그래서
   테스트나 진단에서 동작을 갈아 끼울 때는 ``safety.iter_mounts`` 가 아니라
   **그 함수가 사는 층**(``mounts.iter_mounts``)을 바꿔야 한다 — 층 안에서는
   자기 이웃을 모듈로 찾아 부르기 때문이다. 겉면 이름을 바꾸면 이 모듈을 통해
   부르는 쪽에만 반영되고 층 내부 호출은 옛 함수를 그대로 쓴다.

**automount 보호** — ``/user`` 처럼 아래에 마운트가 잔뜩 달린 자리는 나열하는
것만으로 전부 마운트되면서 시스템이 주저앉는다. 나열만이 아니라 이름 하나를
stat 하는 것도 마찬가지다(``/user/my`` 를 stat = ``my`` 를 마운트해 보라는 뜻).
그래서 동작이 **무엇을 만지게 되는지**로 판정을 나눈다.

======================  ========================  ========================
동작이 만지는 것          자동 (우리가 건다)         사용자 명시 (눌렀다)
======================  ========================  ========================
폴더를 통째로            :func:`may_list`          :func:`may_enter`
                        (+ ``allow_listing``)
경로 이름 하나           :func:`may_stat`          :func:`may_open`
                                                  (+ 끝 구분자 예외)
======================  ========================  ========================

무엇을 위험으로 볼지는 :func:`configure` 의 ``guarded_roots`` (이름으로 지목) ·
``min_depth`` (얕은 자리를 통째로) · ``allow_listing`` (자동완성 전면 차단)이
정하고, autofs 는 설정 없이 마운트 표에서 자동으로 알아본다. 자세한 규칙은
:mod:`~custom_file_dialog.policy` 참고.
"""

from . import policy as _policy
from . import reach as _reach
from .mounts import (
    AUTOMOUNT_FSTYPES,
    MOUNTINFO,
    MOUNTS_TTL,
    REMOTE_FSTYPES,
    clear_mounts,
    has_automounts,
    is_remote,
    iter_mounts,
    mount_for,
    on_automount,
)
from .policy import (
    DEFAULT_ALLOW_LISTING,
    blocks_navigation,
    DEFAULT_MIN_DEPTH,
    guarded_roots,
    is_guarded,
    is_too_shallow,
    listing_allowed,
    may_enter,
    may_list,
    may_open,
    may_stat,
    min_depth,
    path_depth,
    protection_active,
    risky_name,
    risky_place,
)
from .reach import (
    DEFAULT_TIMEOUT,
    DEFAULT_TTL,
    SERVER_PORTS,
    call_with_timeout,
    is_reachable,
    pending_checks,
    probe_host,
    safe_call,
    safe_exists,
    safe_isdir,
    safe_isfile,
    self_check,
    server_of,
)

__all__ = [
    # 설정
    "configure",
    "settings",
    "reset",
    "clear_cache",
    # 정책 판정
    "risky_place",
    "risky_name",
    "may_list",
    "may_enter",
    "may_stat",
    "may_open",
    "protection_active",
    "blocks_navigation",
    "guarded_roots",
    "is_guarded",
    "min_depth",
    "path_depth",
    "is_too_shallow",
    "listing_allowed",
    # 마운트
    "iter_mounts",
    "mount_for",
    "is_remote",
    "on_automount",
    "has_automounts",
    "server_of",
    "REMOTE_FSTYPES",
    "AUTOMOUNT_FSTYPES",
    "MOUNTINFO",
    "MOUNTS_TTL",
    # 도달성 확인
    "is_reachable",
    "self_check",
    "probe_host",
    "call_with_timeout",
    "pending_checks",
    "safe_call",
    "safe_exists",
    "safe_isfile",
    "safe_isdir",
    "SERVER_PORTS",
    "DEFAULT_TIMEOUT",
    "DEFAULT_TTL",
    "DEFAULT_MIN_DEPTH",
    "DEFAULT_ALLOW_LISTING",
]


def configure(
    timeout=None,
    ttl=None,
    guarded_roots=None,
    min_depth=None,
    allow_listing=None,
):
    """확인 방식과 정책을 한 번에 설정한다.

    Args:
        timeout: 한 번의 확인에 기다릴 최대 시간(초).
        ttl: 판정 결과를 재사용할 시간(초). 0 이면 캐시하지 않는다.
        guarded_roots: **그 폴더 자체는 열지 않을** 경로 목록.
            ``/user`` 처럼 아래에 마운트가 잔뜩 달린 자리를 그대로 나열하면
            전부 마운트되면서 시스템이 주저앉는다. 여기에 ``["/user"]`` 를 넣으면
            ``/user`` 는 "접근 불가"로 보고, ``/user/myaccount`` 처럼 **한 단계라도
            아래** 경로는 평소대로 쓴다.
        min_depth: **자동완성이 폴더를 나열할 최소 깊이.** 이보다 얕은 자리는
            나열하지 않아 완성 후보가 뜨지 않는다. 0(기본)이면 제한 없음.

            ``/user`` 처럼 아래에 마운트가 잔뜩 달린 자리는 이름을 미리 다 알기
            어렵다. ``min_depth=2`` 로 두면 ``/user/my`` 까지 쳐도 ``/user`` 를
            나열하지 않으므로 멈추지 않고, ``/user/myaccount/`` 부터 완성이 살아난다.
            그 대신 ``/ho`` → ``/home`` 처럼 **얕은 자리의 완성도 함께 없어진다**.

            깊이가 이 값보다 **작거나 같은** 경로는 어떤 자동 접근도 하지
            않는다 — 나열 · 키 입력마다의 자동 확인 · Enter/열기 확정 · 눌러서
            들어가기까지 전부다. 예외 하나로, **끝에 구분자를 붙인 폴더 표기**는
            명시적 의사로 보고 이 깊이부터 확정을 허용한다(``/user/myaccount/``).
        allow_listing: **자동완성이 폴더 목록을 읽어도 되는지.** ``False`` 면
            깊이와 무관하게 어떤 폴더도 읽지 않아 완성 후보가 아예 뜨지 않는다.
            파일이 수만 개인 폴더처럼 깊이로는 가릴 수 없는 자리까지 막아야 할
            때 쓴다. 위젯 하나만 끄려면 ``FilePathEdit(completer=False)`` 쪽이 낫다.
    """
    _reach.configure(timeout=timeout, ttl=ttl)
    _policy.configure(
        guarded_roots=guarded_roots,
        min_depth=min_depth,
        allow_listing=allow_listing,
    )


def settings():
    """현재 설정 사본 (두 층을 합쳐서 돌려준다)."""
    merged = _reach.settings()
    merged.update(_policy.settings())
    return merged


def reset():
    """모든 설정을 기본값으로 되돌린다(주로 테스트에서)."""
    _reach.reset()
    _policy.reset()


def clear_cache():
    """캐시된 판정과 마운트 표를 모두 지운다(마운트를 고친 뒤 등).

    멈춘 확인 때문에 눌러 둔 마운트도 다시 두드릴 수 있게 푼다 — 최악의 경우
    스레드가 하나 더 생길 뿐이다.
    """
    _reach.clear_cache()
    clear_mounts()
