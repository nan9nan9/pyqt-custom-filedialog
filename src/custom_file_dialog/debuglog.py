"""단계마다 걸린 시간을 ``logging`` 으로 남긴다 — 기본은 꺼져 있다.

    from custom_file_dialog import enable_debug
    enable_debug()                      # 표준 오류로 단계별 시간이 찍힌다

    CustomFileDialog(None, mode="open_file", directory="~/작업")

    DEBUG custom_file_dialog: > 다이얼로그 생성
    DEBUG custom_file_dialog:   > 시작 위치 정하기
    DEBUG custom_file_dialog:     시작 폴더 = /작업
    DEBUG custom_file_dialog:   시작 위치 정하기 ...............      4.0 ms
    DEBUG custom_file_dialog:   > 사이드바 목록 만들기
    DEBUG custom_file_dialog:   사이드바 목록 만들기 ...........     18.4 ms
    DEBUG custom_file_dialog:   훅 설치(가드 · 메뉴 · 표시) ....     12.7 ms
    DEBUG custom_file_dialog: 다이얼로그 생성 .................     56.0 ms mode=open_file

``>`` 로 시작하는 줄은 **들어갔다**는 뜻이고, 짝이 되는 줄이 나오면 그때 걸린
시간이 붙는다. 멈추면 끝 줄이 안 나오므로 **마지막 ``>`` 줄이 멈춘 자리**다.

**끌 때는 값이 0 이다.** 시간을 재는 것부터가 :func:`step` 안에서 일어나므로,
꺼져 있으면 ``perf_counter`` 도 부르지 않는다. 이 패키지는 죽은 네트워크 경로를
다루는 것이 일이라 "재는 비용"이 그대로 GUI 지연이 되기 때문이다.

켜는 방법은 셋이고 전부 같은 것을 켠다.

- :func:`enable_debug` — 코드에서.
- ``CFD_DEBUG=1`` 환경 변수 — 코드를 고치지 않고. 이 모듈을 처음 import 할 때
  본다.
- ``CustomFileDialog(..., debug=True)`` · ``exec_file_dialog(..., debug=True)``
  — 그 한 줄만 고쳐서.

로거 이름은 ``custom_file_dialog`` 다. 앱이 이미 logging 을 설정해 두었다면
:func:`enable_debug` 대신 그 설정에서 이 로거의 수준만 DEBUG 로 올려도 된다
(그때는 핸들러를 따로 붙이지 않는다 — 앱 것을 쓴다).
"""

import logging
import os
import time
import unicodedata
from contextlib import contextmanager

LOGGER_NAME = "custom_file_dialog"

logger = logging.getLogger(LOGGER_NAME)

# 우리가 붙인 핸들러. 다시 켜도 하나만 두려고 들고 있는다.
_handler = None

# 지금 몇 단계 안쪽인지 — 들여쓰기로 중첩을 보여 준다.
_depth = 0

# 이름 뒤 점선을 어디까지 채울지(한글은 두 칸으로 세어 세로가 맞는다)
_LABEL_WIDTH = 44


def enable_debug(enabled=True, stream=None, level=logging.DEBUG):
    """단계별 시간 기록을 켜거나 끈다.

    Args:
        enabled: ``False`` 면 끈다(로거 수준을 되돌리고 우리 핸들러를 뗀다).
        stream: 찍을 곳. None 이면 표준 오류. 앱이 이미 logging 을 설정해
            두었다면 ``stream`` 없이 부르지 말고 그쪽 설정에서 이 로거의
            수준만 올려라 — 여기서 핸들러를 붙이면 같은 줄이 두 번 찍힌다.
        level: 켤 때 로거에 줄 수준.

    Returns:
        지금 켜져 있는지(bool).
    """
    global _handler
    if not enabled:
        if _handler is not None:
            logger.removeHandler(_handler)
            _handler = None
        logger.setLevel(logging.NOTSET)
        return False

    logger.setLevel(level)
    if _handler is None and not logger.handlers:
        # 앱이 이미 핸들러를 달아 두었으면 그대로 쓴다(중복 출력 방지).
        _handler = logging.StreamHandler(stream)
        _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(_handler)
    return True


def is_enabled():
    """지금 단계별 시간이 찍히는지."""
    return logger.isEnabledFor(logging.DEBUG)


@contextmanager
def step(label, **detail):
    """``with step("사이드바 목록 만들기"):`` — 나올 때 걸린 시간을 찍는다.

    꺼져 있으면 **아무것도 하지 않는다**(시각도 읽지 않는다).
    예외로 빠져나가도 그때까지 걸린 시간을 남긴다 — 멈춘 마운트에서 죽었을 때
    어디까지 갔는지가 알고 싶은 것이기 때문이다.
    """
    if not is_enabled():
        yield
        return

    global _depth
    indent = "  " * _depth
    # 들어갈 때도 한 줄 남긴다. **멈추면 끝 줄이 영영 안 나오므로**, 어디서
    # 멈췄는지는 이 줄로만 알 수 있다 — 죽은 마운트를 다루는 것이 이 패키지의
    # 일이라 그 한 줄이 가장 쓸모 있다.
    logger.debug("%s> %s", indent, label)
    _depth += 1
    started = time.perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        spent = (time.perf_counter() - started) * 1000
        _depth -= 1
        extra = "".join(" %s=%s" % (k, v) for k, v in detail.items())
        if failed:
            extra += " (중간에 실패)"
        logger.debug("%s%s %8.1f ms%s", indent, _dotted(label, len(indent)),
                     spent, extra)


def _dotted(label, indent_width):
    """``이름 ......`` — 시간 칸이 세로로 맞도록 점을 채운다."""
    room = max(4, _LABEL_WIDTH - indent_width - _width(label))
    return "%s %s" % (label, "." * room)


def _width(text):
    """터미널에서 차지하는 칸 수 — 한글·기호는 두 칸으로 본다."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def log(message, *args):
    """단계가 아닌 한 줄짜리 사실(개수 · 판정 결과 등)."""
    if is_enabled():
        logger.debug("%s%s", "  " * _depth, message % args if args else message)


if os.environ.get("CFD_DEBUG", "").strip() not in ("", "0", "false", "False"):
    enable_debug()
