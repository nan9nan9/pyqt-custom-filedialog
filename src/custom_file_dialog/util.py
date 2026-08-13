"""여러 모듈이 되풀이하던 잔손질을 한곳에 모은다."""

import os
import re

from qtpy.QtCore import QUrl

# URL 스킴 판별용. 스킴을 2글자 이상으로 제한해 윈도우 드라이브 문자("C:\\...")를
# 스킴으로 오인하지 않게 한다. ("file:", "ftp://" 는 매칭, "C:" 는 비매칭)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]+:")


def abspath(path):
    """``~`` 를 펴고 절대·정규 경로로 만든다. 빈 값이면 None."""
    if not path:
        return None
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def url_path(url):
    """``QUrl`` 이든 문자열이든 로컬 경로 문자열로. 없으면 빈 문자열."""
    if url is None:
        return ""
    if hasattr(url, "toLocalFile"):
        return url.toLocalFile() or ""
    return str(url)


def exec_menu(menu, point):
    """메뉴를 띄우고, 닫힌 뒤 **정리까지 예약한다**.

    Qt5 는 ``exec_``, Qt6 은 ``exec`` — 그 차이를 흡수한다. 메뉴는 오래 사는
    뷰를 부모로 만들어지므로, 놔두면 우클릭할 때마다 하나씩 쌓인다(다이얼로그를
    닫지 않고 계속 쓰는 앱에서 눈에 띈다). 결과를 읽은 뒤 지운다.
    """
    run = getattr(menu, "exec_", None) or menu.exec
    try:
        return run(point)
    finally:
        menu.deleteLater()


def to_urls(items):
    """경로 문자열 / QUrl 이 섞인 목록을 QUrl 리스트로 맞춘다.

    ``"~/작업"`` 같은 ``~`` 표기는 홈 디렉터리로 펼쳐지고, ``"file:///..."``
    이나 ``"file:"`` 처럼 스킴이 붙은 문자열은 URL 로 그대로 해석한다.
    (``"file:"`` 은 사이드바의 "Computer" 항목이다.)
    """
    urls = []
    for item in items or []:
        if isinstance(item, QUrl):
            urls.append(item)
            continue
        text = str(item)
        if not text:
            continue
        if _SCHEME_RE.match(text):
            urls.append(QUrl(text))
        else:
            urls.append(QUrl.fromLocalFile(os.path.expanduser(text)))
    return urls
