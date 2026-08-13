"""최근 선택 경로 / 마지막 방문 폴더 기억.

``settings_key`` 를 주면 :class:`~qtpy.QtCore.QSettings` 에 저장되어 프로그램을
다시 켜도 유지되고, 주지 않으면 위젯이 살아 있는 동안만 메모리에 남는다.

**``settings_key`` 하나가 자리 하나**다. 입력 CSV 를 고르는 자리와 결과를 저장하는
자리에 서로 다른 이름을 주면 각자 마지막에 쓰던 폴더에서 열린다::

    FilePathEdit(mode="open_file", settings_key="입력csv")
    CustomFileDialog(mode="save_file", settings_key="결과저장")
    exec_file_dialog(mode="save_file", settings_key="결과저장")

위젯이든 다이얼로그든 **같은 이름이면 같은 기억을 공유**한다. 화면에 남아 있지
않는 일회성 다이얼로그도 :func:`last_dir` · :func:`remember_dir` 로 같은 저장소를
쓴다. 이름은 미리 등록할 것 없이 아무 문자열이나 정하면 된다.
"""

import os

from qtpy.QtCore import QSettings

from . import safety
from .constants import DEFAULT_HISTORY_SIZE

# configure_settings() 로 지정하는 기본 QSettings 정보
_DEFAULT_ORG = None
_DEFAULT_APP = None


def configure_settings(organization, application=None):
    """settings_key 만 준 위젯들이 공용으로 쓸 QSettings 정보를 지정한다.

    호출하지 않으면 ``QApplication`` 에 설정된 organizationName /
    applicationName 을 그대로 쓴다(둘 다 없으면 저장되지 않을 수 있다).
    """
    global _DEFAULT_ORG, _DEFAULT_APP
    _DEFAULT_ORG = organization
    _DEFAULT_APP = application


def default_settings():
    """configure_settings() 로 지정한 정보를 바탕으로 QSettings 를 만든다."""
    if _DEFAULT_ORG:
        if _DEFAULT_APP:
            return QSettings(_DEFAULT_ORG, _DEFAULT_APP)
        return QSettings(_DEFAULT_ORG, _DEFAULT_ORG)
    return QSettings()


class PathHistory:
    """최근 경로 목록과 마지막 폴더를 관리한다.

    Args:
        key: QSettings 에 저장할 키 접두사. None 이면 메모리에만 유지.
        max_items: 기억할 최근 경로 개수.
        settings: 직접 만든 QSettings 인스턴스(테스트/커스텀 저장 위치용).
    """

    def __init__(self, key=None, max_items=DEFAULT_HISTORY_SIZE, settings=None):
        self.key = key
        self.max_items = max(0, int(max_items))
        self._settings = settings
        self._items = []
        self._last_dir = None
        if self.key:
            self._load()

    # ------------------------------------------------------------- 저장소
    def _store(self):
        """쓸 QSettings. **직접 준 것이 없으면 그때그때 만든다.**

        한 번 만들어 붙들고 있으면, 위젯을 만든 **뒤에**
        :func:`configure_settings` 를 부른 앱에서 그 위젯만 옛 저장소를 계속
        써서 "같은 이름이면 같은 기억을 공유한다"는 약속이 조용히 깨진다.
        QSettings 만들기는 값싸다.
        """
        if not self.key:
            return None
        return self._settings if self._settings is not None else default_settings()

    def _load(self):
        store = self._store()
        if store is None:
            return
        items = store.value("custom_file_dialog/%s/recent" % self.key, [])
        # QSettings 는 항목이 1개면 문자열로 돌려주는 바인딩이 있어 보정한다.
        if isinstance(items, str):
            items = [items]
        self._items = [str(i) for i in (items or []) if i][: self.max_items]
        last = store.value("custom_file_dialog/%s/last_dir" % self.key, None)
        self._last_dir = str(last) if last else None

    def _save_items(self):
        store = self._store()
        if store is not None:
            store.setValue("custom_file_dialog/%s/recent" % self.key, list(self._items))

    def _save_last_dir(self):
        store = self._store()
        if store is not None:
            store.setValue(
                "custom_file_dialog/%s/last_dir" % self.key, self._last_dir or ""
            )

    # --------------------------------------------------------------- API
    def items(self):
        """최신순 최근 경로 리스트."""
        return list(self._items)

    def add(self, path):
        """경로를 최근 목록 맨 앞에 추가한다(중복은 위로 끌어올림)."""
        if not path or self.max_items <= 0:
            return
        path = str(path)
        if path in self._items:
            self._items.remove(path)
        self._items.insert(0, path)
        del self._items[self.max_items :]
        self._save_items()

    def clear(self):
        self._items = []
        self._save_items()

    def last_dir(self):
        """직전에 다이얼로그를 닫았던 폴더."""
        return self._last_dir

    def set_last_dir(self, directory):
        """마지막 폴더만 기록한다.

        최근 경로 목록은 **건드리지 않는다.** 같은 키를 max_items 가 다른 곳에서
        함께 쓸 수 있는데(위젯은 history=30, :func:`remember_dir` 헬퍼는 기본값),
        여기서 목록까지 다시 쓰면 작은 쪽 기준으로 잘려 나간다.
        """
        if directory:
            self._last_dir = str(directory)
            self._save_last_dir()


# ------------------------------------------------- 용도별 시작 위치 (키 하나 = 용도 하나)
def last_dir(settings_key, settings=None):
    """그 이름으로 **마지막에 쓰던 폴더** (기억이 없으면 None).

    ``FilePathEdit(settings_key=...)`` · ``CustomFileDialog(settings_key=...)``
    가 쓰는 저장소와 같은 곳을 본다. 그래서 위젯에서 고른 결과를 일회성
    다이얼로그가 이어받고, 그 반대도 된다.

    Args:
        settings_key: 자리를 구분할 이름. ``"입력csv"``, ``"결과저장"`` 처럼
            자리마다 다르게 준다. 미리 등록할 것 없이 아무 문자열이나 된다.
        settings: 직접 만든 QSettings (테스트/커스텀 저장 위치용).
    """
    if not settings_key:
        return None
    return PathHistory(key=settings_key, settings=settings).last_dir()


def remember_dir(settings_key, path, settings=None):
    """그 이름의 마지막 폴더를 기록한다.

    ``path`` 가 파일이면 **그 파일이 있는 폴더**를 기억한다. 최근 경로 목록은
    건드리지 않는다(다이얼로그는 목록을 쓰지 않으므로).

    Returns:
        실제로 기억한 폴더 (기억할 것이 없으면 None).
    """
    if not settings_key or not path:
        return None
    path = str(path)
    # 고른 경로가 죽은 원격 마운트일 수 있다 — 평범한 isdir 은 안 돌아온다
    directory = path if safety.safe_isdir(path) else os.path.dirname(path)
    if not directory:
        return None
    PathHistory(key=settings_key, settings=settings).set_last_dir(directory)
    return directory
