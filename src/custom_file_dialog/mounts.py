"""마운트 표를 읽어 **경로의 성격**만 알려 준다 — 가장 아래 층.

``/proc/self/mountinfo`` 를 문자열로 읽을 뿐이라 **파일시스템을 절대 건드리지
않는다.** 그래서 죽은 마운트 위의 경로를 물어도 멈추지 않는다. 설정
(:mod:`~custom_file_dialog.policy`)도, 도달성 확인
(:mod:`~custom_file_dialog.reach`)도 이 층 위에 얹힌다.

    is_remote(path)        서버가 죽으면 멈출 수 있는 자리인가 (NFS · CIFS …)
    on_automount(path)     건드리면 마운트가 붙는 자리인가 (autofs)
    mount_for(path)        그 경로가 속한 (마운트지점, 종류, 원본)
"""

import os
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

MOUNTINFO = "/proc/self/mountinfo"

# 마운트 표를 다시 읽는 주기(초). 경로 확인이 잦아도 부담이 없게 짧게 캐시한다.
MOUNTS_TTL = 5.0

_mounts = {"list": None, "stamp": 0.0}


def _abspath(path):
    """``~`` 를 펴고 절대·정규 경로로. (util.abspath 와 같지만 Qt 를 끌어오지 않는다)

    ``bytes`` 는 ``os.fsdecode`` 로 푼다. ``str()`` 로 감싸면 ``b'/user'`` 라는
    **글자 그대로의** 이름이 되어, 막아 달라고 준 자리가 조용히 안 막힌다.
    """
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


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

    # 마운트 경로에 비UTF-8 바이트가 섞일 수 있다(옛 공유의 EUC-KR 이름 등).
    # 순정 utf-8 로 읽으면 UnicodeDecodeError 가 나는데 그것은 OSError 가 아니라,
    # 여기서 새면 mount_for -> may_stat/may_enter/safe_* 가 전부 터진다(= 다이얼로그
    # 생성과 키 입력마다 크래시). 바이트를 그대로 오가는 surrogateescape 로 읽는다.
    try:
        with open(MOUNTINFO, encoding="utf-8", errors="surrogateescape") as handle:
            lines = handle.readlines()
    except (OSError, ValueError):
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
            # 같은 지점에 여러 줄이 있으면 **나중에 나온 것**이 위에 겹친
            # 실효 마운트다. systemd automount 는 트리거된 뒤에도 autofs 줄이
            # 남고 그 위에 실제 파일시스템이 올라오므로, 먼저 나온 것을 고르면
            # 이미 붙어서 잘 도는 폴더를 "아직 안 붙은 automount" 로 오판한다.
            if len(normalized) >= best_length:
                best, best_length = entry, len(normalized)
    return best


def is_remote(path):
    """원격 파일시스템 위의 경로인지."""
    mount = mount_for(path)
    return bool(mount) and mount[1] in REMOTE_FSTYPES


def on_automount(path):
    """autofs 마운트 위의 경로인지 (지점 자체든 그 아래든).

    autofs 위에서는 나열도, 자식 stat 도, 아직 안 붙은 하위 폴더를 여는 것도
    전부 마운트 시도가 된다. 이미 다른 종류(nfs 등)로 붙은 하위 경로는 그
    마운트가 더 길게 잡히므로 여기서 걸리지 않는다.
    """
    mount = mount_for(path)
    return bool(mount) and mount[1] in AUTOMOUNT_FSTYPES


def is_automount_point(path):
    """그 자리 **자체**가 automount(autofs) 지점인지.

    지점 자체를 여는 것은 "그 아래 이름을 전부 마운트해 보라"는 뜻이라 가장
    위험하다. 반면 그 아래의 특정 이름(``/user/myaccount``)을 여는 것은 **하나만**
    마운트하는 일이라, 사용자가 명시적으로 지정했다면 의도된 동작이다.
    :func:`on_automount` 가 둘을 함께 묶는 것과 달리 여기서는 지점만 가린다.
    """
    mount = mount_for(path)
    return (
        bool(mount)
        and mount[1] in AUTOMOUNT_FSTYPES
        and os.path.normpath(mount[0]) == _abspath(path)
    )


def has_automounts():
    """시스템에 automount(autofs) 마운트가 하나라도 있는지."""
    return any(fstype in AUTOMOUNT_FSTYPES for _point, fstype, _src in iter_mounts())


def table_available():
    """마운트 표를 읽을 수 있는 시스템인지.

    ``/proc/self/mountinfo`` 가 없는 곳(윈도우 · macOS · /proc 없는 컨테이너)
    에서는 어떤 경로가 원격인지 알 수 없다. 그 경우 "로컬이니 그냥 만져도
    된다"고 볼 수 없다 — 죽은 UNC 경로 하나로 GUI 가 멈춘다.
    """
    return bool(iter_mounts())


def clear_mounts():
    """캐시해 둔 마운트 표를 버린다(마운트를 고친 뒤 등)."""
    _mounts["list"] = None
    _mount_keys.clear()
