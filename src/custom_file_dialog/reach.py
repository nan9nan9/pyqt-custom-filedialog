"""죽은 원격 마운트에서 **GUI 가 멈추지 않게** 경로를 확인한다.

NFS 같은 하드 마운트에서는 서버가 응답하지 않으면 ``os.stat()`` 이 커널 안에서
**중단 불가능 대기(D 상태)** 로 들어간다. 시그널로도 깨울 수 없어서, 그 호출을
한 스레드/프로세스는 마운트가 살아날 때까지 돌아오지 않는다. 그래서 타임아웃
하나만으로는 부족하고, 세 가지를 겹쳐서 쓴다.

1. **마운트 판별** — 그 경로가 어느 마운트에 속하는지, 원격인지 본다
   (:mod:`~custom_file_dialog.mounts`). **파일시스템을 건드리지 않는다.**
2. **소켓 프로브** — 원격이면 서버에 TCP 연결만 시도해 본다. 역시 파일시스템을
   건드리지 않으므로 절대 멈추지 않고, 방화벽에 막혀 있으면 타임아웃으로 바로
   판별된다. 서버는 마운트 정보에서 자동으로 알아내 **종류에 맞는 포트**로만
   두드린다(NFS 2049 · CIFS 445 · sshfs 22, :data:`SERVER_PORTS`).
3. **스레드 + 타임아웃** — 위를 통과했을 때만 실제 ``os.stat()`` 을 별도 스레드에서
   돌리고 정해진 시간만 기다린다. 블로킹 I/O 중에는 GIL 이 풀리므로 그 스레드가
   못 돌아와도 GUI 는 계속 움직인다. (프로세스를 죽이는 방식은 D 상태에서는
   SIGKILL 조차 밀리므로 쓰지 않는다.)

멈춘 확인 스레드가 쌓이지도 않는다 — 실제로 만져 보는 확인은 **마운트당 한
번에 하나만** 돌고(:func:`call_with_timeout` 의 ``pending_key``), 그 스레드가
돌아오기 전에는 같은 마운트를 다시 두드리지 않고 곧바로 실패로 판정한다.
판정 결과는 **마운트 단위로 캐시**해서 죽은 서버를 매번 다시 두드리지 않는다.

정책상 건드리면 안 되는 자리(:mod:`~custom_file_dialog.policy`)는 여기까지
오지도 않는다 — :func:`safe_call` 이 곧바로 기본값을 돌려준다.
"""

import os
import socket
import threading
import time

from . import mounts, policy
from .mounts import AUTOMOUNT_FSTYPES, REMOTE_FSTYPES

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

# 동시에 멈춰 있어도 되는 확인 스레드 수의 상한. 멈춘 스레드는 D 상태라 죽일
# 수 없으므로, 늘지 않게 하는 방법은 새로 만들지 않는 것뿐이다. 묶음 키가
# 잡지 못한 경우의 마지막 방어라 넉넉히 잡는다.
MAX_PENDING_CHECKS = 8

_lock = threading.Lock()
_settings = {"timeout": DEFAULT_TIMEOUT, "ttl": DEFAULT_TTL}
_cache = {}                 # key -> (판정, 시각)
_pending = set()            # 아직 돌아오지 않은(=멈춘) 확인 스레드
_pending_keys = set()       # 멈춘 확인이 걸려 있는 마운트지점 — 다시 안 두드린다


def configure(timeout=None, ttl=None):
    """확인 방식을 바꾼다 (인자 설명은 :func:`custom_file_dialog.safety.configure`)."""
    with _lock:
        if timeout is not None:
            _settings["timeout"] = float(timeout)
        if ttl is not None:
            _settings["ttl"] = float(ttl)
        _cache.clear()


def settings():
    """현재 확인 설정 사본."""
    with _lock:
        return dict(_settings)


def reset():
    """확인 설정을 기본값으로 되돌린다."""
    with _lock:
        _settings.update(timeout=DEFAULT_TIMEOUT, ttl=DEFAULT_TTL)
        _cache.clear()


def clear_cache():
    """캐시된 판정을 지우고, 멈춘 확인 때문에 눌러 둔 마운트도 푼다."""
    with _lock:
        _cache.clear()
        _pending_keys.clear()


def pending_checks():
    """아직 돌아오지 않은(=멈춘) 확인 스레드 수. 진단용."""
    with _lock:
        return len(_pending)


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

    with _lock:
        if key is not None and key in _pending_keys:
            return False, None              # 이미 멈춘 확인이 있다 -> 안 두드린다
        if len(_pending) >= MAX_PENDING_CHECKS:
            # 묶음 키가 못 잡은 폭주의 마지막 방어. 키를 아무리 잘 잡아도
            # "어디까지가 한 마운트인지" 모르는 경우가 있어, 스레드 수 자체에
            # 상한을 둔다(멈춘 스레드는 죽일 수 없으므로 안 만드는 수밖에 없다).
            return False, None

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
        # 예외도 "끝내지 못한 것"으로 본다. True 로 돌려주면 호출자가 그 값을
        # 결과로 믿어(None) 문서에 적힌 default 대신 None 이 흘러나간다.
        return False, None
    return True, box.get("value")


# --------------------------------------------------------------- 판정
# 경로를 만졌을 때 무슨 일이 나는지에 따른 네 갈래. is_reachable 과 safe_call 이
# 같은 분기를 공유한다(마운트 표 조회도 한 번만 한다).
_BLOCKED = "blocked"        # 차단 경로 · automount 위 — 만지는 것 자체가 금지
_LOCAL = "local"            # 곧바로 만져도 되는 곳
_REMOTE = "remote"          # 서버가 죽으면 멈출 수 있는 곳 — 프로브+타임아웃
_UNKNOWN = "unknown"        # 마운트 표가 없어 알 수 없는 곳 — 타임아웃만 씌운다


def _classify(path):
    """경로를 ``( _BLOCKED | _LOCAL | _REMOTE, 마운트 )`` 로 가른다.

    지목한 자리(``guarded_roots``) **자체**만 막는다. 그 아래는 평소대로
    확인한다 — ``/user`` 는 열지 않아도 ``/user/myaccount`` 는 사용자의 홈이라
    시작 폴더로도 쓰고 유효성도 봐야 하기 때문이다.

    :func:`~custom_file_dialog.policy.may_stat` 이 같은 경로를 "안 된다"고
    하는 것과 어긋나 보이지만, **묻는 것이 다르다.**

    - ``may_stat`` — "타이핑 도중 **자동으로** 만져도 되나?" 글자마다 도는
      일이라 오타 이름(``/user/my``)이 automounter 를 헛돌게 한다. 그래서
      부모가 위험하면 전부 막는다.
    - 여기 — "이 경로를 확인해 달라"는 **호출자의 요청**을 어떻게 처리하나.
      자동으로 도는 자리는 호출자가 ``may_stat`` 을 먼저 보고 부르지 않는다
      (validators · 타이핑 가드가 그렇게 한다). 여기까지 온 요청은 확정됐거나
      앱이 기억해 둔 경로라, 한 번의 마운트는 의도된 것이다.
    """
    if policy.is_guarded(path):
        return _BLOCKED, None                # 그 자리 자체는 열지 않는다
    mount = mounts.mount_for(path)
    if mount is None:
        # 마운트 표를 **읽을 수 있는** 시스템에서 못 찾았으면 진짜 로컬이다.
        # 표 자체가 없는 곳(윈도우 · macOS · /proc 없는 컨테이너)에서는 알 수
        # 없으므로 스레드+타임아웃으로 감싼다 — 그러지 않으면 죽은 UNC 경로
        # 하나로 GUI 가 그대로 멈춘다(문서가 약속한 "다른 OS 에서는 3단계
        # 타임아웃만 동작한다"가 지켜지지 않았다).
        return (_LOCAL if mounts.table_available() else _UNKNOWN), None
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
    if kind == _UNKNOWN:
        # 예외를 내지 않는 프로브를 쓴다. os.stat 은 **없는 경로**에서도 예외를
        # 내는데 call_with_timeout 은 그것을 "못 끝냈다"로 보므로, 아직 만들지
        # 않은 저장 대상이 마운트 표 없는 곳(윈도우 등)에서만 "도달 불가"가
        # 됐다 — 같은 경로가 리눅스에서는 True 라 판정이 플랫폼마다 갈렸다.
        finished, _value = call_with_timeout(
            os.path.lexists, path, timeout=timeout, pending_key=_unknown_key(path)
        )
        return finished
    return _mount_reachable(mount, timeout, use_cache)


def _unknown_key(path):
    """마운트 표가 없을 때 쓸 "멈춘 확인" 묶음 키 — **담고 있는 폴더**.

    묶음 키는 "한 번 멈추면 여기는 당분간 두드리지 않는다"는 뜻이다. 어디까지가
    한 마운트인지 모르는 상황이라 경계를 넓게 잡으면 안 된다 — 파일시스템
    뿌리로 묶으면, 마운트 표가 없는 POSIX(macOS · ``/proc`` 없는 컨테이너)는
    **모든 경로가 이 갈래로 오므로** 죽은 공유 하나를 확인한 순간 프로세스의
    모든 경로 확인이 실패로 떨어진다(멀쩡한 로컬 파일이 "존재하지 않습니다"가
    되고 다이얼로그가 cwd 에서 열린다).

    담고 있는 폴더로 묶으면 막으려던 것은 그대로 막힌다 — 스레드가 쌓이는
    경우는 입력창에 한 글자씩 치는 상황이고, 그때 만들어지는 경로들은 모두
    같은 폴더 아래다. 그 밖의 폭주는 :data:`MAX_PENDING_CHECKS` 가 받는다.
    """
    return os.path.dirname(os.path.abspath(path)) or os.sep


def _mount_reachable(mount, timeout=None, use_cache=True):
    """원격 마운트 하나의 판정 — 캐시를 먼저 보고, 없으면 실제로 확인한다.

    캐시 키에 **제한 시간까지** 넣는다. 그러지 않으면 짧게 잡은 자리 하나가
    느린(살아 있는) 서버를 "죽음"으로 판정했을 때, 그 답이 TTL 동안 프로세스
    전체에 퍼져 멀쩡한 파일이 "존재하지 않습니다"로 거절된다.
    """
    mountpoint, fstype, source = mount
    wait = _settings["timeout"] if timeout is None else float(timeout)
    key = (mountpoint, round(wait, 3))
    if use_cache:
        cached = _cached(key)
        if cached is not None:
            return cached
    result = self_check(mountpoint, source, timeout, fstype=fstype)
    _remember(key, result)
    return result


def self_check(mountpoint, source, timeout=None, fstype=None):
    """캐시 없이 실제로 확인한다(서버 프로브 → stat).

    ``fstype`` 이 :data:`SERVER_PORTS` 에 있으면 그 종류에 맞는 포트로 서버를
    두드리고, 없는(모르는) 종류는 서버 프로브를 건너뛰고 stat 으로만 판정한다.
    """
    wait = _settings["timeout"] if timeout is None else float(timeout)

    # 1) 마운트한 서버부터 — 종류에 맞는 포트로만.
    #    socket 의 timeout 은 **연결에만** 걸리고 이름 조회(getaddrinfo)에는
    #    걸리지 않는다. VPN 이 끊겨 DNS 가 죽으면 리졸버 기본 대기(수 초)만큼
    #    GUI 가 그대로 멈춘다 — "절대 멈추지 않는다"는 이 단계의 약속이 깨진다.
    #    그래서 프로브도 스레드+타임아웃 안에서 돌린다.
    port = SERVER_PORTS.get(fstype)
    if port is not None:
        host = server_of(source, fstype)
        if host:
            finished, reachable = call_with_timeout(
                probe_host, host, port, wait, timeout=wait, pending_key=mountpoint
            )
            if not finished or not reachable:
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
    if kind == _UNKNOWN:
        # 원격인지 알 수 없다 — 프로브할 서버도 모르니 타임아웃만 씌운다.
        # 멈춘 확인은 볼륨 단위로 묶는다(마운트 표가 있을 때의 마운트지점 몫).
        finished, value = call_with_timeout(
            func, path, timeout=timeout, pending_key=_unknown_key(path)
        )
        return value if finished else default

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
