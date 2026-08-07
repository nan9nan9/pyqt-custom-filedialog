"""최근 선택 경로 / 마지막 방문 폴더 기억.

``settings_key`` 를 주면 :class:`~qtpy.QtCore.QSettings` 에 저장되어 프로그램을
다시 켜도 유지되고, 주지 않으면 위젯이 살아 있는 동안만 메모리에 남는다.
"""

from qtpy.QtCore import QSettings

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
        if not self.key:
            return None
        if self._settings is None:
            self._settings = default_settings()
        return self._settings

    def _load(self):
        store = self._store()
        if store is None:
            return
        items = store.value("file_dialog_widget/%s/recent" % self.key, [])
        # QSettings 는 항목이 1개면 문자열로 돌려주는 바인딩이 있어 보정한다.
        if isinstance(items, str):
            items = [items]
        self._items = [str(i) for i in (items or []) if i][: self.max_items]
        last = store.value("file_dialog_widget/%s/last_dir" % self.key, None)
        self._last_dir = str(last) if last else None

    def _save(self):
        store = self._store()
        if store is None:
            return
        store.setValue("file_dialog_widget/%s/recent" % self.key, list(self._items))
        store.setValue("file_dialog_widget/%s/last_dir" % self.key, self._last_dir or "")

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
        self._save()

    def clear(self):
        self._items = []
        self._save()

    def last_dir(self):
        """직전에 다이얼로그를 닫았던 폴더."""
        return self._last_dir

    def set_last_dir(self, directory):
        if directory:
            self._last_dir = str(directory)
            self._save()
