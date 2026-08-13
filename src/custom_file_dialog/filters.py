"""QFileDialog 필터 문자열을 만들고 해석하는 헬퍼.

Qt 의 필터 문자열은 ``"이미지 (*.png *.jpg);;모든 파일 (*)"`` 형태라 직접
쓰기에 번거롭다. 여기서는 ``[("이미지", ["png", "jpg"]), ...]`` 같은 파이썬
자료구조로도 필터를 지정할 수 있게 해 준다.
"""

import os
import re

from .constants import ALL_FILES_FILTER

# "이미지 파일 (*.png *.jpg)" 에서 괄호 안의 패턴들을 뽑아내는 정규식
_PATTERN_RE = re.compile(r"\(([^()]*)\)\s*$")


def build_filter(filters, add_all_files=False):
    """필터 목록을 Qt 필터 문자열로 변환한다.

    받을 수 있는 형태::

        build_filter("이미지 (*.png);;모든 파일 (*)")     # 이미 문자열이면 그대로
        build_filter([("이미지", ["png", "jpg"])])        # (설명, 확장자목록)
        build_filter([("이미지", "*.png *.jpg")])         # (설명, 패턴문자열)
        build_filter(["*.png", "*.txt"])                  # 패턴만 나열
        build_filter({"이미지": ["png"], "문서": ["txt"]}) # dict (파이썬 3.7+ 순서 유지)

    Args:
        filters: 위 형태 중 하나. None 이면 None 반환(=필터 없음).
        add_all_files: True 면 "모든 파일 (*)" 항목을 맨 뒤에 덧붙인다.

    Returns:
        Qt 필터 문자열 또는 None.
    """
    if filters is None:
        entries = []
    elif isinstance(filters, str):
        # 이미 Qt 필터 문자열. add_all_files 만 처리한다.
        if not filters.strip():
            # 빈 문자열에 덧붙이면 ";;모든 파일 (*)" 이 되어 필터 콤보 맨 앞에
            # 빈 항목이 생긴다(filters=값 or "" 관용구에서 걸린다).
            return _entry_to_string(ALL_FILES_FILTER) if add_all_files else None
        if add_all_files and "(*)" not in filters:
            return "%s;;%s" % (filters, _entry_to_string(ALL_FILES_FILTER))
        return filters
    elif isinstance(filters, dict):
        entries = list(filters.items())
    else:
        entries = list(filters)

    if add_all_files and not any(_is_all_files(e) for e in entries):
        entries.append(ALL_FILES_FILTER)

    parts = [_entry_to_string(entry) for entry in entries]
    parts = [p for p in parts if p]
    return ";;".join(parts) if parts else None


def _entry_to_string(entry):
    """필터 항목 하나를 ``"설명 (*.ext ...)"`` 문자열로 만든다."""
    if isinstance(entry, str):
        # "*.png" 처럼 패턴만 온 경우 -> 설명을 확장자에서 만들어 붙인다
        pattern = entry.strip()
        if not pattern:
            return ""
        if "(" in pattern:      # 이미 "설명 (*.png)" 형태
            return pattern
        suffix = suffix_of_pattern(pattern)
        label = "%s 파일" % suffix.upper() if suffix else "파일"
        return "%s (%s)" % (label, pattern)

    label, patterns = entry
    if isinstance(patterns, str):
        pattern_text = patterns.strip()
    else:
        pattern_text = " ".join(_as_pattern(p) for p in patterns)
    return "%s (%s)" % (label, pattern_text or "*")


def _as_pattern(value):
    """``"png"`` / ``".png"`` / ``"*.png"`` 를 모두 ``"*.png"`` 로 맞춘다."""
    text = str(value).strip()
    if not text or text == "*":
        return "*"
    if text.startswith("*."):
        return text
    if text.startswith("."):
        return "*" + text
    if "*" in text or "?" in text:      # "log_*.txt" 같은 임의 패턴은 그대로
        return text
    return "*." + text


def _is_all_files(entry):
    return "*" in split_patterns(_entry_to_string(entry))


def split_patterns(filter_entry):
    """``"이미지 (*.png *.jpg)"`` 에서 ``["*.png", "*.jpg"]`` 를 뽑는다."""
    if not filter_entry:
        return []
    match = _PATTERN_RE.search(filter_entry.strip())
    text = match.group(1) if match else filter_entry
    return [p for p in text.replace(",", " ").split() if p]


def suffix_of(filter_entry):
    """필터 항목의 대표 확장자를 반환한다 (``"이미지 (*.png ...)"`` -> ``"png"``).

    **확장자 패턴(**\ ``*.ext``\ **)일 때만** 값을 준다. ``*`` 는 물론
    ``*lib``/``*_corner`` (접미사) · ``lib_*`` (접두사) 같은 패턴도 None 이다 —
    저장 모드에서 붙여 줄 "확장자"가 아니어서, 붙이면 ``foo._corner`` 처럼
    필터에도 안 걸리는 이름이 되기 때문이다.
    """
    for pattern in split_patterns(filter_entry):
        suffix = suffix_of_pattern(pattern)
        if suffix:
            return suffix
    return None


def suffix_of_pattern(pattern):
    """``"*.tar.gz"`` -> ``"tar.gz"``. 확장자 패턴이 아니면 None.

    ``"*"`` · ``"*lib"`` · ``"lib_*"`` · ``"*.c*"`` 전부 None — 점(.) 뒤에
    와일드카드 없는 이름이 오는 ``"*.ext"`` 꼴만 확장자로 본다.
    """
    text = pattern.strip()
    if not text.startswith("*."):
        return None
    text = text[2:]
    if not text or "*" in text or "?" in text:
        return None
    return text


def ensure_suffix(path, suffix):
    """확장자가 없는 경로에 ``suffix`` 를 붙여 준다.

    이미 어떤 확장자든 붙어 있으면 그대로 둔다(사용자가 명시한 것을 존중).
    네이티브 다이얼로그는 확장자를 알아서 붙여 주기도 하지만 플랫폼마다
    동작이 달라, 반환된 경로에 대해 한 번 더 보정한다.
    """
    if not path or not suffix:
        return path
    # 점으로 끝나면 splitext 가 ext=".", 즉 "확장자가 있다"로 보이지만 실제로는
    # 이름이 덜 적힌 것이다. 그 점을 떼고 붙여 준다.
    text = path.rstrip(".") if path.endswith(".") else path
    if os.path.splitext(text)[1]:
        return path
    return "%s.%s" % (text, suffix.lstrip("."))
