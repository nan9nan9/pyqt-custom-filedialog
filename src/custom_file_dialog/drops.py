"""끌어다 놓은 것을 **그 모드가 받을 수 있는 경로**로 바꾼다.

드롭은 위젯이 이벤트로 받지만, "무엇을 받아 줄 것인가"는 Qt 와 무관한 규칙이다.

- 로컬 파일이 아닌 것(웹 URL 등)은 버린다
- 폴더 모드에 **파일**을 떨어뜨리면 그 파일이 든 **폴더**로 받아 준다
- 파일 모드에 **폴더**를 떨어뜨리면 받지 않는다
- 여러 개 모드가 아니면 첫 번째만 남긴다

폴더인지 확인하는 일만 바깥에서 받는다(``isdir``). 위젯은 죽은 마운트에서
멈추지 않는 판을 넘기고, 테스트는 가짜를 넘겨 파일시스템 없이 규칙만 볼 수
있다.
"""

import os

from .constants import SelectMode, is_multi_mode
from .util import url_path


def acceptable_paths(urls, mode, isdir):
    """드래그된 URL 목록에서 그 모드가 받을 경로만 골라 낸다.

    Args:
        urls: ``QUrl`` 목록(또는 경로 문자열 목록).
        mode: :class:`~custom_file_dialog.constants.SelectMode` 값.
        isdir: 폴더인지 판정할 함수 — 보통
            :func:`~custom_file_dialog.validators.isdir_check` 가 만들어 준 것.

    Returns:
        받을 수 있는 경로 리스트(없으면 빈 리스트).
    """
    paths = []
    for url in urls or []:
        path = url_path(url)
        if not path:
            continue                    # 로컬 파일이 아니다
        if mode == SelectMode.DIRECTORY:
            path = path if isdir(path) else os.path.dirname(path)
            if not path:
                continue
        elif isdir(path):
            continue                    # 파일 모드에 폴더는 받지 않는다
        paths.append(path)

    if not paths:
        return []
    return paths if is_multi_mode(mode) else paths[:1]
