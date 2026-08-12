"""입력된 경로의 유효성을 판정한다.

위젯이 테두리 색/툴팁으로 알려 줄 ``(유효한가, 사유)`` 를 만들어 주는 순수
함수 모음이라, Qt 없이도 테스트할 수 있다.
"""

import os

from . import safety
from .constants import DEFAULT_MUST_EXIST, SelectMode


def _checks(timeout):
    """``(isdir, isfile, exists)`` 를 돌려준다.

    ``timeout`` 이 None 이면 평범한 ``os.path`` 함수, 값이 있으면 죽은 네트워크
    경로에서 멈추지 않는 :mod:`~custom_file_dialog.safety` 판 함수를 쓴다.
    """
    if timeout is None:
        return os.path.isdir, os.path.isfile, os.path.exists
    return (
        lambda p: safety.safe_isdir(p, timeout),
        lambda p: safety.safe_isfile(p, timeout),
        lambda p: safety.safe_exists(p, timeout),
    )


def split_paths(text, separator="; "):
    """한 줄에 들어 있는 여러 경로를 리스트로 나눈다.

    구분자 주변 공백은 제거하고, 빈 항목은 버린다. 경로 자체에 구분자가
    들어 있으면 나눌 방법이 없으므로, 그런 경우엔 ``separator`` 를 파일명에
    쓰이지 않는 문자로 바꿔서 쓴다.
    """
    if not text:
        return []
    sep = (separator or "; ").strip() or ";"
    return [part.strip() for part in text.split(sep) if part.strip()]


def join_paths(paths, separator="; "):
    """경로 리스트를 한 줄 문자열로 합친다."""
    return (separator or "; ").join(paths or [])


def validate_paths(
    paths, mode=SelectMode.OPEN_FILE, must_exist=None, required=False, timeout=None
):
    """경로 목록의 유효성을 판정한다.

    Args:
        paths: 검사할 경로 리스트.
        mode: :class:`~custom_file_dialog.constants.SelectMode` 값.
        must_exist: True 면 실제로 존재해야 유효하다고 본다.
            None 이면 모드별 기본값(save_file 만 False)을 쓴다.
        required: True 면 비어 있는 것도 오류로 본다.
        timeout: 값을 주면 네트워크 경로를 만질 때 그 시간(초) 안에 응답이
            없으면 "없는 경로"로 본다(죽은 NFS 등에서 멈추지 않게).

    Returns:
        ``(valid, reason)``. valid 가 True 면 reason 은 빈 문자열.
    """
    if must_exist is None:
        must_exist = DEFAULT_MUST_EXIST.get(mode, True)
    paths = [p for p in (paths or []) if p]

    if not paths:
        if required:
            return False, "경로를 지정해야 합니다."
        return True, ""

    if mode != SelectMode.OPEN_FILES and len(paths) > 1:
        return False, "이 항목에는 경로를 하나만 지정할 수 있습니다."

    for path in paths:
        valid, reason = _validate_one(path, mode, must_exist, timeout)
        if not valid:
            return False, reason

    return True, ""


def _validate_one(path, mode, must_exist, timeout=None):
    expanded = os.path.expanduser(path)

    # 만져 보는 것만으로 automount 가 붙는 자리(부모가 guarded/얕음/autofs)는
    # 존재 확인을 하지 않는다. 키 입력마다 불리는 함수라, 여기서 stat 하면
    # "/user/j" 처럼 미완성 경로 한 글자마다 마운트 시도가 일어난다. 판정을
    # 보류하고 유효로 둔다 — 확정 시점의 확인은 다이얼로그/앱 몫이다.
    if not safety.may_stat(expanded):
        return True, ""

    isdir, isfile, exists = _checks(timeout)

    if mode == SelectMode.DIRECTORY:
        if isdir(expanded):
            return True, ""
        if not must_exist:
            return _check_parent(expanded, timeout)
        if exists(expanded):
            return False, "폴더가 아니라 파일입니다: %s" % path
        return False, "폴더가 존재하지 않습니다: %s" % path

    # 파일 계열 모드
    if isdir(expanded):
        return False, "파일이 아니라 폴더입니다: %s" % path

    if isfile(expanded):
        return True, ""

    if must_exist:
        return False, "파일이 존재하지 않습니다: %s" % path

    # save_file 처럼 아직 없어도 되는 경우 -> 상위 폴더는 있어야 한다
    return _check_parent(expanded, timeout)


def _check_parent(path, timeout=None):
    isdir, _isfile, _exists = _checks(timeout)
    parent = os.path.dirname(os.path.abspath(path))
    if isdir(parent):
        return True, ""
    return False, "상위 폴더가 존재하지 않습니다: %s" % parent
