"""느리거나 죽은 네트워크 경로에서 GUI 가 멈추지 않도록 미리 걸러 낸다.

NFS 같은 하드 마운트에서는 서버가 응답하지 않으면 ``os.stat()`` 이 커널 안에서
**중단 불가능 대기(D 상태)** 로 들어간다. 시그널로도 깨울 수 없어서, 그 호출을
한 스레드/프로세스는 마운트가 살아날 때까지 돌아오지 않는다. 그래서 타임아웃
하나만으로는 부족하고, 세 가지를 겹쳐서 쓴다.

1. **마운트 판별** — ``/proc/self/mountinfo`` 를 읽어 그 경로가 어느 마운트에
   속하는지, 원격 파일시스템인지 본다. **파일시스템을 건드리지 않는다.**
2. **소켓 프로브** — 원격이면 서버에 TCP 연결만 시도해 본다. 역시 파일시스템을
   건드리지 않으므로 절대 멈추지 않고, 방화벽에 막혀 있으면 타임아웃으로 바로
   판별된다. 서버는 마운트 정보에서 자동으로 알아내 **종류에 맞는 포트**로만
   두드린다(NFS 2049 · CIFS 445 · sshfs 22, :data:`SERVER_PORTS`).
3. **스레드 + 타임아웃** — 위를 통과했을 때만 실제 ``os.stat()`` 을 별도 스레드에서
   돌리고 정해진 시간만 기다린다. 블로킹 I/O 중에는 GIL 이 풀리므로 그 스레드가
   못 돌아와도 GUI 는 계속 움직인다. (프로세스를 죽이는 방식은 D 상태에서는
   SIGKILL 조차 밀리므로 쓰지 않는다.)

그리고 ``/user`` 처럼 **아래에 마운트가 잔뜩 달린 자리**는 나열하는 것만으로 전부
마운트되면서 시스템이 주저앉는다. 막는 방법이 두 가지다.

- :func:`configure` 의 ``guarded_roots`` — 위험한 자리를 **이름으로 지목**한다.
  ``/user`` 는 열지 않고 ``/user/myaccount`` 처럼 한 단계라도 아래면 평소대로 쓴다.
- :func:`configure` 의 ``min_depth`` — 자동완성이 **얕은 자리는 아예 나열하지
  않게** 한다. ``2`` 를 주면 ``/user`` (1단계)를 나열하지 않으므로 ``/user/my`` 까지
  쳐도 조용하고, ``/user/myaccount`` (2단계)부터 완성이 살아난다. 위험한 자리를 미리
  다 적어 두기 어려울 때 쓰는 그물이다.
- :func:`configure` 의 ``allow_listing`` — ``False`` 면 **깊이와 무관하게 어떤
  폴더도 읽지 않는다.** 파일이 수만 개인 폴더처럼 깊이로는 가릴 수 없는 자리까지
  막아야 할 때 쓰는 마지막 스위치다.

나열만이 아니라 **한 경로를 만져 보는 것(stat)** 도 automount 아래에서는
마운트를 부른다. ``/user/my`` 를 stat 하면 automounter 가 ``j`` 라는 이름을
마운트하려고 시도한다 — 경로를 한 글자씩 칠 때마다 이 일이 일어나면 그것만으로
시스템이 주저앉는다. 그래서 "입력 중인 경로를 **자동으로** 만져 봐도 되는가"를
:func:`may_stat` 하나로 판정한다. 부모 폴더가 ``guarded_roots`` 이거나, 깊이가
``min_depth`` 보다 **작거나 같거나**, **autofs 마운트 위**면 False — 그때는
스레드+타임아웃 확인조차 하지 않고 **디스크를 아예 만지지 않는다.** autofs 는
설정 없이도 ``/proc/self/mountinfo`` 에서 자동으로 알아본다.

멈춘 확인 스레드가 쌓이지도 않는다 — 죽은 원격 마운트를 실제로 만져 보는
확인은 **마운트당 한 번에 하나만** 돌고(:func:`call_with_timeout` 의
``pending_key``), 그 스레드가 돌아오기 전에는 같은 마운트를 다시 두드리지
않고 곧바로 실패로 판정한다.

판정 결과는 **마운트 단위로 캐시**해서, 죽은 서버를 매번 다시 두드리지 않는다.

    from custom_file_dialog import safety

    safety.configure(timeout=1.0, guarded_roots=["/user"], min_depth=2)
    if safety.is_reachable("/nfs/proj/a.csv"):
        ...
"""

import os
import socket
import threading
import time

# 원격(= 서버가 죽으면 멈출 수 있는) 파일시스템 종류
REMOTE_FSTYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "afs",
        "ceph",
        "glusterfs",
        "lustre",
        "fuse.sshfs",
        "fuse.davfs",
        "fuse.s3fs",
    }
)

# 건드리는 것만으로 마운트가 붙는(= 자식 stat 이 마운트 시도가 되는) 종류.
# systemd .automount 유닛과 autofs 데몬 모두 mountinfo 에 "autofs" 로 나온다.
AUTOMOUNT_FSTYPES = frozenset({"autofs"})

# 원격 종류별로 살펴볼 서버 포트. **모르는 종류는 소켓 프로브를 건너뛰고**
# 스레드+타임아웃 stat 만으로 판정한다 — 엉뚱한 포트를 두드리면 멀쩡한 서버가
# 거부(ECONNREFUSED)해서 "죽었다"로 오판하기 때문이다(예: CIFS 서버는 2049 를
# 듣지 않는다). 프로브를 건너뛰어도 stat 이 타임아웃으로 지켜 주므로 GUI 는
# 멈추지 않고, 죽은 마운트 판별이 한 타임아웃만큼 느려질 뿐이다.
SERVER_PORTS = {
    "nfs": 2049,
    "nfs4": 2049,
    "cifs": 445,
    "smbfs": 445,
    "smb3": 445,
    "fuse.sshfs": 22,
}

DEFAULT_TIMEOUT = 1.0       # 한 번의 확인에 기다릴 최대 시간(초)
DEFAULT_TTL = 30.0          # 판정 결과를 재사용할 시간(초)

# 자동완성이 폴더를 나열할 최소 깊이. 0 이면 제한 없음(어디서든 완성한다).
DEFAULT_MIN_DEPTH = 0

# 자동완성이 폴더 목록을 읽어도 되는지. False 면 깊이와 무관하게 하나도 안 읽는다.
DEFAULT_ALLOW_LISTING = True

MOUNTINFO = "/proc/self/mountinfo"

# 마운트 표를 다시 읽는 주기(초). 경로 확인이 잦아도 부담이 없게 짧게 캐시한다.
MOUNTS_TTL = 5.0

_lock = threading.Lock()
_settings = {
    "timeout": DEFAULT_TIMEOUT,
    "ttl": DEFAULT_TTL,
    "guarded_roots": [],
    "min_depth": DEFAULT_MIN_DEPTH,
    "allow_listing": DEFAULT_ALLOW_LISTING,
}
_cache = {}                 # key -> (판정, 시각)
_mounts = {"list": None, "stamp": 0.0}
_pending = set()            # 아직 돌아오지 않은(=멈춘) 확인 스레드 이름
_pending_keys = set()       # 멈춘 확인이 걸려 있는 마운트지점 — 다시 안 두드린다


def _abspath(path):
    """``~`` 를 펴고 절대·정규 경로로. (util.abspath 와 같지만 Qt 를 끌어오지 않는다)"""
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def configure(
    timeout=None,
    ttl=None,
    guarded_roots=None,
    min_depth=None,
    allow_listing=None,
):
    """확인 방식을 설정한다.

    Args:
        timeout: 한 번의 확인에 기다릴 최대 시간(초).
        ttl: 판정 결과를 재사용할 시간(초). 0 이면 캐시하지 않는다.
        guarded_roots: **그 폴더 자체는 열지 않을** 경로 목록.
            ``/user`` 처럼 아래에 마운트가 잔뜩 달린 자리를 그대로 나열하면
            전부 마운트되면서 시스템이 주저앉는다. 여기에 ``["/user"]`` 를 넣으면
            ``/user`` 는 "접근 불가"로 보고, ``/user/myaccount`` 처럼 **한 단계라도 아래**
            경로는 평소대로 쓴다.
        min_depth: **자동완성이 폴더를 나열할 최소 깊이.** 이보다 얕은 자리는
            나열하지 않아 완성 후보가 뜨지 않는다. 0(기본)이면 제한 없음.

            ``/user`` 처럼 아래에 마운트가 잔뜩 달린 자리는 이름을 미리 다 알기
            어렵다. ``min_depth=2`` 로 두면 ``/user/my`` 까지 쳐도 ``/user`` 를
            나열하지 않으므로 멈추지 않고, ``/user/myaccount/`` 부터 완성이 살아난다.
            그 대신 ``/ho`` → ``/home`` 처럼 **얕은 자리의 완성도 함께 없어진다**.

            깊이가 이 값보다 **작거나 같은** 경로는 어떤 자동 접근도 하지
            않는다 — 나열(:func:`may_list`) · 키 입력마다의 자동 확인
            (:func:`may_stat`) · **Enter/열기 버튼으로 확정하는 것**
            (:func:`may_open`)까지 전부다. 확정의 stat 한 번도 automount
            아래에서는 마운트 시도라 그 한 번으로 멈추기 때문이다.

            예외 하나 — **끝에 구분자를 붙인 폴더 표기는 명시적 의사**로 보고
            이 깊이부터 허용한다. ``min_depth=2`` 기준: ``/user/my`` 와
            ``/user/myaccount`` 는 Enter 로 안 열리고(안내 팝업이 뜬다),
            ``/user/myaccount/`` 와 ``/user/myaccount/a.csv`` 는 열린다. 다이얼로그에서
            폴더를 **눌러** 들어가는 것은 막지 않는다 — 그쪽까지 막으려면
            ``guarded_roots`` 를 함께 쓴다.
        allow_listing: **자동완성이 폴더 목록을 읽어도 되는지.** ``False`` 면
            깊이와 무관하게 어떤 폴더도 읽지 않아 완성 후보가 아예 뜨지 않는다.
            자동완성을 통째로 끄는 스위치다.

            파일이 수만 개인 폴더나 automount 가 잔뜩 달린 자리는 완성 후보를
            만들려고 목록을 읽는 것만으로 오래 걸린다. 깊이로 가릴 수 없는
            자리까지 확실히 막아야 할 때 쓴다. 위젯 하나만 끄려면
            ``FilePathEdit(completer=False)`` 쪽이 낫다.
    """
    with _lock:
        if timeout is not None:
            _settings["timeout"] = float(timeout)
        if ttl is not None:
            _settings["ttl"] = float(ttl)
        if guarded_roots is not None:
            _settings["guarded_roots"] = [
                _abspath(p) for p in guarded_roots if str(p).strip()
            ]
        if min_depth is not None:
            _settings["min_depth"] = max(0, int(min_depth))
        if allow_listing is not None:
            _settings["allow_listing"] = bool(allow_listing)
        _cache.clear()


def settings():
    """현재 설정 사본."""
    with _lock:
        return dict(_settings, guarded_roots=list(_settings["guarded_roots"]))


def reset():
    """모든 설정을 기본값으로 되돌린다(주로 테스트에서)."""
    with _lock:
        _settings.update(
            timeout=DEFAULT_TIMEOUT,
            ttl=DEFAULT_TTL,
            guarded_roots=[],
            min_depth=DEFAULT_MIN_DEPTH,
            allow_listing=DEFAULT_ALLOW_LISTING,
        )
        _cache.clear()


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


def may_list(path):
    """그 폴더를 자동완성이 읽어도 되는지 — 세 설정과 automount 를 한 번에 본다.

    ``allow_listing`` 이 꺼져 있거나, ``guarded_roots`` 에 지목됐거나,
    ``min_depth`` 보다 얕거나, **autofs 마운트 위**면 False.
    (autofs 는 항목을 나열해 stat 하는 것만으로 전부 마운트되고, 아직 안 붙은
    하위 폴더는 나열하려면 먼저 마운트해야 한다 — 설정이 없어도 mountinfo 에서
    자동으로 알아보고 아예 읽지 않는다.)
    """
    return (
        listing_allowed()
        and not is_guarded(path)
        and not is_too_shallow(path)
        and not on_automount(path)
    )


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
        guarded_roots() or min_depth() or not listing_allowed() or has_automounts()
    )


def has_automounts():
    """시스템에 automount(autofs) 마운트가 하나라도 있는지."""
    return any(fstype in AUTOMOUNT_FSTYPES for _point, fstype, _src in iter_mounts())


def on_automount(path):
    """autofs 마운트 위의 경로인지 (지점 자체든 그 아래든).

    autofs 위에서는 나열도, 자식 stat 도, 아직 안 붙은 하위 폴더를 여는 것도
    전부 마운트 시도가 된다. 이미 다른 종류(nfs 등)로 붙은 하위 경로는 그
    마운트가 더 길게 잡히므로 여기서 걸리지 않는다.
    """
    mount = mount_for(path)
    return bool(mount) and mount[1] in AUTOMOUNT_FSTYPES


def may_stat(path):
    """입력 중인 경로를 **자동으로** 만져(stat) 봐도 되는지.

    타이핑 도중의 미완성 경로를 키 입력마다 stat 하면, automount 아래에서는
    글자마다 마운트 시도가 일어난다(``/user/my`` → ``j`` 마운트 시도). 다음이면
    False — **디스크를 아예 만지지 않는다** (스레드+타임아웃 확인조차 안 한다).

    - 부모 폴더가 ``guarded_roots`` 로 지목된 자리
    - 깊이가 ``min_depth`` 보다 **작거나 같음** (``min_depth=2`` 면 ``/user/my``
      까지 금지, ``/user/myaccount/x`` 부터 허용)
    - autofs 마운트 위 (지점 자체든 아직 안 붙은 그 아래든 — 설정 없이 자동)

    자동 확인(자동완성 · 키 입력마다의 유효성 표시)에만 쓰는 판정이다. 사용자가
    Enter 나 버튼으로 **확정**하는 쪽은 :func:`may_open` 이 따로 판정한다 —
    깊은 경로의 의도한 마운트(``/user/myaccount/a.csv``)는 확정의 stat 한 번으로
    일어나면 된다.
    """
    if not path:
        return True
    absolute = _abspath(path)
    if is_guarded(os.path.dirname(absolute)):
        return False
    limit = min_depth()
    if limit > 0 and path_depth(absolute) <= limit:
        return False
    return not on_automount(absolute)


def may_enter(path):
    """그 자리를 **열어(=들어가) 되는지** — 들어가면 통째로 나열되기 때문이다.

    폴더를 여는 순간 Qt 는 그 안을 전부 읽어 항목마다 stat 한다. automount
    아래에서는 그것이 곧 "그 폴더의 모든 이름을 마운트"다. 그래서 나열 판정
    (:func:`may_list`)과 같은 기준을 쓴다 — ``guarded_roots`` 로 지목된 자리,
    ``min_depth`` 보다 얕은 자리, autofs 마운트 위면 False.

    ``allow_listing`` 은 보지 않는다. 그 설정은 **자동완성**을 끄는 스위치이지,
    사용자가 눌러서 들어가는 것까지 막는 뜻이 아니다.
    """
    return not (
        is_guarded(path) or is_too_shallow(path) or on_automount(path)
    )


def may_open(path):
    """그 경로를 **확정**(Enter · 열기 버튼)해도 되는지.

    확정은 Qt 가 GUI 스레드에서 그 경로를 곧바로 stat 하는 일이라, automount
    아래의 얕은 미완성 경로(``/user/my``)에 Enter 를 치면 자동 확인을 다 막아도
    그 한 번으로 멈춘다. 규칙:

    - ``guarded_roots`` 로 지목한 자리 → 항상 False (구분자 여부와 무관)
    - 깊이가 ``min_depth`` 보다 깊음 → True (``/user/myaccount/a.csv``)
    - 깊이가 ``min_depth`` **이상**이고 끝에 구분자를 붙인 폴더 표기 → True
      — ``/user/myaccount/`` 는 "이 폴더를 열겠다"는 **명시적 의사**로 본다.
      의도한 마운트는 이 확정의 stat 한 번으로 일어난다.
    - 그 외(구분자 없는 얕은 경로) → False — ``/user/myaccount`` 도 ``/user/myacc``
      도 Enter 로는 안 열린다. 폴더로 가려면 끝에 구분자를 붙인다.
    """
    if not path:
        return True
    text = str(path).strip()
    explicit_dir = text.endswith(("/", os.sep))
    absolute = _abspath(text)
    if is_guarded(absolute):
        return False
    limit = min_depth()
    if limit <= 0:
        return True
    depth = path_depth(absolute)
    return depth > limit or (explicit_dir and depth >= limit)


def clear_cache():
    """캐시된 판정과 마운트 표를 모두 지운다(마운트를 고친 뒤 등).

    멈춘 확인 때문에 눌러 둔 마운트(``pending_key``)도 다시 두드릴 수 있게
    푼다 — 최악의 경우 스레드가 하나 더 생길 뿐이다.
    """
    with _lock:
        _cache.clear()
        _pending_keys.clear()
    _mounts["list"] = None
    _mount_keys.clear()


def pending_checks():
    """아직 돌아오지 않은(=멈춘) 확인 스레드 수. 진단용."""
    with _lock:
        return len(_pending)


# --------------------------------------------------------------- 마운트
# mountinfo 는 경로 안의 특수 문자를 8진수로 이스케이프해 적는다(man 5 proc).
# 풀지 않으면 공백이 든 마운트("/mnt/my share")를 어떤 경로와도 못 맞춰서
# 원격 판별이 조용히 실패하고, 그 마운트에는 안전장치가 걸리지 않는다.
# 역슬래시(\134)는 마지막에 풀어야 "\134040" 같은 조합을 다시 해석하지 않는다.
_MOUNT_ESCAPES = (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\"))


def _unescape_mount(field):
    for code, char in _MOUNT_ESCAPES:
        field = field.replace(code, char)
    return field


def iter_mounts(refresh=False):
    """``[(마운트지점, 종류, 원본)]`` 목록. 읽지 못하면 빈 목록.

    경로 확인이 잦으므로 :data:`MOUNTS_TTL` 동안 캐시한다.
    """
    now = time.time()
    if not refresh and _mounts["list"] is not None:
        if now - _mounts["stamp"] <= MOUNTS_TTL:
            return _mounts["list"]

    try:
        with open(MOUNTINFO, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        _mounts["list"], _mounts["stamp"] = [], now
        return []

    mounts = []
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        # 마운트지점은 fields[4] 다. 구분자가 그보다 앞이거나(깨진 줄) 구분자
        # 뒤에 종류·원본이 없으면 건너뛴다 — 어떤 줄에서도 예외가 나면 안 된다.
        if separator < 5 or len(fields) < separator + 3:
            continue
        mounts.append(
            (
                _unescape_mount(fields[4]),
                fields[separator + 1],
                _unescape_mount(fields[separator + 2]),
            )
        )
    _mounts["list"], _mounts["stamp"] = mounts, now
    return mounts


# 마운트지점 문자열 -> (정규화 경로, 하위 판별용 접두사). 마운트지점은 몇 개
# 안 되는 고정 문자열이라 무한히 자라지 않는다. mount_for 가 키 입력마다,
# 그리고 자동완성 모델의 인덱스마다 불리므로 normpath 반복을 여기서 없앤다.
_mount_keys = {}


def _mount_key(mountpoint):
    keys = _mount_keys.get(mountpoint)
    if keys is None:
        normalized = os.path.normpath(mountpoint)
        keys = _mount_keys[mountpoint] = (
            normalized,
            normalized.rstrip(os.sep) + os.sep,
        )
    return keys


def mount_for(path):
    """경로가 속한 ``(마운트지점, 종류, 원본)`` (못 찾으면 None).

    경로 문자열만 보고 **가장 긴 마운트지점**을 고른다. 파일시스템을 건드리지
    않으므로 죽은 마운트에서도 안전하다.
    """
    if not path:
        return None
    absolute = _abspath(path)
    best = None
    best_length = -1
    for entry in iter_mounts():
        normalized, prefix = _mount_key(entry[0])
        if absolute == normalized or absolute.startswith(prefix):
            if len(normalized) > best_length:
                best, best_length = entry, len(normalized)
    return best


def is_remote(path):
    """원격 파일시스템 위의 경로인지."""
    mount = mount_for(path)
    return bool(mount) and mount[1] in REMOTE_FSTYPES


def server_of(source, fstype=None):
    """마운트 원본에서 서버 호스트를 뽑는다 (없으면 None).

    ``server:/export`` (NFS) · ``[fe80::1]:/export`` (IPv6) ·
    ``//server/share`` (CIFS) · ``user@host:/dir`` (sshfs) 형태를 안다.
    """
    if not source:
        return None
    text = str(source)
    if text.startswith("//") or text.startswith("\\\\"):        # CIFS
        rest = text[2:].replace("\\", "/")
        host = rest.split("/", 1)[0]
        return host or None
    if text.startswith("["):                                    # IPv6 NFS
        # "[fe80::1]:/export" — 주소 안에 콜론이 있어 대괄호 먼저 벗긴다
        end = text.find("]")
        if end > 1:
            return text[1:end]
        return None
    if ":" in text:                                             # NFS
        host = text.split(":", 1)[0]
        # "sshfs" 처럼 user@host 형태도 있다
        if "@" in host:
            host = host.split("@", 1)[1]
        return host or None
    return None


# --------------------------------------------------------------- 프로브
def probe_host(host, port, timeout=None):
    """서버에 TCP 연결만 시도해 본다. 파일시스템을 건드리지 않는다.

    Returns:
        연결되면 True. 거부/타임아웃/이름 못 찾음이면 False.
    """
    if not host:
        return True
    wait = _settings["timeout"] if timeout is None else float(timeout)
    try:
        with socket.create_connection((host, int(port)), timeout=wait):
            return True
    except OSError:
        # 거부(ECONNREFUSED)든 타임아웃이든, 그 서버에 기대면 안 되는 상태다
        return False


# ------------------------------------------------------- 스레드 + 타임아웃
def call_with_timeout(func, *args, **kwargs):
    """``func`` 을 별도 스레드에서 돌리고 정해진 시간만 기다린다.

    ``pending_key`` 를 주면(보통 마운트지점) **그 키의 확인이 아직 멈춰 있는
    동안에는 새 스레드를 만들지 않고** 곧바로 ``(False, None)`` 을 돌려준다.
    죽은 마운트 위의 경로를 키 입력마다 확인해도 멈춘 스레드는 마운트당
    한 개뿐이고, 그 스레드가 돌아오면 다시 실제로 확인한다.

    Returns:
        ``(끝났는가, 반환값)``. 시간 안에 못 끝내면 ``(False, None)`` 이고,
        그 스레드는 그대로 둔다(D 상태라 죽일 수 없다). 블로킹 I/O 중에는
        GIL 이 풀려 있으므로 호출한 쪽은 계속 움직인다.
    """
    timeout = kwargs.pop("timeout", None)
    key = kwargs.pop("pending_key", None)
    wait = _settings["timeout"] if timeout is None else float(timeout)

    if key is not None:
        with _lock:
            if key in _pending_keys:
                return False, None          # 이미 멈춘 확인이 있다 -> 안 두드린다

    box = {}
    done = threading.Event()

    def run():
        try:
            box["value"] = func(*args, **kwargs)
        except Exception as exc:            # noqa: BLE001 (호출자에게 값으로 전달)
            box["error"] = exc
        finally:
            done.set()
            with _lock:
                _pending.discard(threading.current_thread())
                if key is not None:
                    _pending_keys.discard(key)

    thread = threading.Thread(target=run, daemon=True)
    with _lock:
        _pending.add(thread)
    thread.start()

    if not done.wait(wait):
        if key is not None:
            with _lock:
                # 방금 끝났으면(finally 가 먼저 돌았으면) 키를 남기지 않는다
                if not done.is_set():
                    _pending_keys.add(key)
        return False, None                  # 멈췄다 -> 스레드는 두고 나온다
    if "error" in box:
        return True, None
    return True, box.get("value")


# --------------------------------------------------------------- 판정
# 경로를 만졌을 때 무슨 일이 나는지에 따른 네 갈래. is_reachable 과 safe_call 이
# 같은 분기를 공유한다(마운트 표 조회도 한 번만 한다).
_BLOCKED = "blocked"        # 차단 경로 · automount 위 — 만지는 것 자체가 금지
_LOCAL = "local"            # 곧바로 만져도 되는 곳
_REMOTE = "remote"          # 서버가 죽으면 멈출 수 있는 곳 — 프로브+타임아웃


def _classify(path):
    """경로를 ``( _BLOCKED | _LOCAL | _REMOTE, 마운트 )`` 로 가른다."""
    if is_guarded(path):
        return _BLOCKED, None                # 그 자리 자체는 열지 않는다
    mount = mount_for(path)
    if mount is None:
        return _LOCAL, None                  # 마운트 표에 없으면 로컬로 본다
    if mount[1] in AUTOMOUNT_FSTYPES:
        return _BLOCKED, mount               # 만지는 것 자체가 마운트 시도다
    if mount[1] in REMOTE_FSTYPES:
        return _REMOTE, mount
    return _LOCAL, mount


def is_reachable(path, timeout=None, use_cache=True):
    """그 경로를 만져도 멈추지 않을지 판정한다.

    로컬 경로는 곧바로 True, 차단 경로와 autofs 위는 만지지 않고 False.
    원격이면 서버 소켓 프로브 → 실제 ``os.stat()`` (스레드+타임아웃) 순으로
    확인하고, 결과를 마운트 단위로 캐시한다.

    Args:
        path: 확인할 경로.
        timeout: 이번 확인에만 쓸 제한 시간(초).
        use_cache: 캐시된 판정을 재사용할지.

    Returns:
        만져도 되면 True, 멈출 것 같으면 False.
    """
    kind, mount = _classify(path)
    if kind == _BLOCKED:
        return False
    if kind == _LOCAL:
        return True
    return _mount_reachable(mount, timeout, use_cache)


def _mount_reachable(mount, timeout=None, use_cache=True):
    """원격 마운트 하나의 판정 — 캐시를 먼저 보고, 없으면 실제로 확인한다."""
    mountpoint, fstype, source = mount
    if use_cache:
        cached = _cached(mountpoint)
        if cached is not None:
            return cached
    result = self_check(mountpoint, source, timeout, fstype=fstype)
    _remember(mountpoint, result)
    return result


def self_check(mountpoint, source, timeout=None, fstype=None):
    """캐시 없이 실제로 확인한다(서버 프로브 → stat).

    ``fstype`` 이 :data:`SERVER_PORTS` 에 있으면 그 종류에 맞는 포트로 서버를
    두드리고, 없는(모르는) 종류는 서버 프로브를 건너뛰고 stat 으로만 판정한다.
    """
    wait = _settings["timeout"] if timeout is None else float(timeout)

    # 1) 마운트한 서버부터 — 종류에 맞는 포트로만
    port = SERVER_PORTS.get(fstype)
    if port is not None:
        host = server_of(source, fstype)
        if host and not probe_host(host, port, wait):
            return False

    # 2) 여기까지 통과했으면 실제로 만져 본다(멈춰도 GUI 는 안 멈추고,
    #    같은 마운트에서 멈춘 확인이 있으면 스레드를 더 만들지 않는다)
    finished, _value = call_with_timeout(
        os.stat, mountpoint, timeout=wait, pending_key=mountpoint
    )
    return finished


def _cached(key):
    ttl = _settings["ttl"]
    if ttl <= 0:
        return None
    with _lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    result, stamp = entry
    if time.time() - stamp > ttl:
        return None
    return result


def _remember(key, result):
    if _settings["ttl"] <= 0:
        return
    with _lock:
        _cache[key] = (result, time.time())


# ------------------------------------------------- 파일시스템 확인 감싸기
def safe_call(func, path, default=False, timeout=None):
    """``func(path)`` 을 안전하게 부른다. 멈출 것 같으면 ``default``.

    ``os.path.isfile`` / ``isdir`` / ``exists`` 를 그대로 넘겨 쓰면 된다.

    **로컬 경로는 그대로 호출한다.** 멈출 일이 없는데 스레드를 만들면 입력 한 자마다
    스레드가 생기는 꼴이라, 원격 마운트일 때만 프로브와 타임아웃을 거친다.
    차단 경로와 autofs 위 경로는 아예 만지지 않고 ``default`` 다.
    """
    kind, mount = _classify(path)
    if kind == _BLOCKED:
        return default
    if kind == _LOCAL:
        return func(path)

    if not _mount_reachable(mount, timeout):
        return default
    # 같은 마운트에서 이미 멈춘 확인이 있으면 스레드를 더 만들지 않는다
    finished, value = call_with_timeout(
        func, path, timeout=timeout, pending_key=mount[0]
    )
    return value if finished else default


def safe_exists(path, timeout=None):
    return safe_call(os.path.exists, path, False, timeout)


def safe_isfile(path, timeout=None):
    return safe_call(os.path.isfile, path, False, timeout)


def safe_isdir(path, timeout=None):
    return safe_call(os.path.isdir, path, False, timeout)
