"""끌어다 놓은 것을 **그 모드가 받을 수 있는 경로**로 바꾼다.

드롭은 위젯이 이벤트로 받지만, "무엇을 받아 줄 것인가"는 Qt 와 무관한 규칙이다.

- 로컬 파일이 아닌 것(웹 URL 등)은 버린다
- 폴더 모드에 **파일**을 떨어뜨리면 그 파일이 든 **폴더**로 받아 준다
- 파일 모드에 **폴더**를 떨어뜨리면 받지 않는다
- **어느 쪽인지 확인하지 못한 경로는 받지 않는다** (automount 위 · 죽은 원격 ·
  차단 경로). 넘겨 주거나 부모로 바꾸면 사용자가 떨어뜨린 적 없는 경로가
  입력창에 들어가고, 앱은 그것을 사용자가 고른 것으로 믿는다.
- 여러 개 모드가 아니면 첫 번째만 남긴다

무엇인지 확인하는 일만 바깥에서 받는다(``isdir`` · ``isfile``). 위젯은 죽은
마운트에서 멈추지 않는 판을 넘기고, 테스트는 가짜를 넘겨 파일시스템 없이
규칙만 볼 수 있다.
"""

import os

from .constants import SelectMode, is_multi_mode
from .util import url_path


def acceptable_paths(urls, mode, isdir, isfile=None):
    """드래그된 URL 목록에서 그 모드가 받을 경로만 골라 낸다.

    Args:
        urls: ``QUrl`` 목록(또는 경로 문자열 목록).
        mode: :class:`~custom_file_dialog.constants.SelectMode` 값.
        isdir: 폴더인지 판정할 함수 — 보통
            :func:`~custom_file_dialog.validators.isdir_check` 가 만들어 준 것.
        isfile: 파일인지 판정할 함수. 폴더 모드에서 **부모로 바꿔 받을지**를
            정할 때만 쓴다. 주지 않으면 예전처럼 "폴더가 아니면 파일"로 본다.

    Returns:
        받을 수 있는 경로 리스트(없으면 빈 리스트).
    """
    paths = []
    for url in urls or []:
        path = url_path(url)
        if not path:
            continue                    # 로컬 파일이 아니다
        if mode == SelectMode.DIRECTORY:
            if not isdir(path):
                # 여기서 넘어온 False 는 "파일이다"일 수도, "확인하지 못했다"
                # 일 수도 있다(죽은 원격 · automount · 차단 경로에서 safe_* 는
                # 둘 다 False 다). 확인 수단이 있는데 파일도 아니라면 **버린다**
                # — 부모로 바꾸면 사용자가 떨어뜨린 적 없는 경로가 조용히
                # 입력창에 들어가고, 앱은 그 폴더에 산출물을 쓴다.
                if isfile is not None and not isfile(path):
                    continue
                path = os.path.dirname(path)
                if not path:
                    continue
        elif isfile is not None:
            # 파일 모드 — "폴더가 아니다"가 아니라 **파일이 맞다**로 판정한다.
            # isdir 만 보면 확인하지 못한 자리(automount 위 등)가 False 로
            # 돌아와 폴더가 파일 칸에 그대로 들어갔다(폴더 모드와 기준이
            # 갈리던 자리다 — 어느 쪽이든 모르면 받지 않는다).
            if not isfile(path):
                continue
        elif isdir(path):
            continue                    # 파일 모드에 폴더는 받지 않는다
        paths.append(path)

    if not paths:
        return []
    return paths if is_multi_mode(mode) else paths[:1]
