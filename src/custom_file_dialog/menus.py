"""QFileDialog 우클릭 메뉴: 즐겨찾기 추가 / 분류 삭제 / 최근 목록 비우기.

두 곳에 메뉴를 건다.

**파일 목록**(오른쪽)에서 파일이나 폴더를 우클릭하면 맨 위에
``즐겨찾기에 추가 ▸`` 가 붙는다. 기존 분류를 고르거나 새 분류를 만들 수 있다.
그 아래에는 Qt 기본 메뉴 항목(이름 변경 · 삭제 · 숨김 파일 · 새 폴더)이 그대로
따라붙는다.

**분류 폴더 안**(즐겨찾기 분류 · 최근 파일)에서는 Qt 기본 "삭제" 대신
``'설계'에서 제거`` · ``'최근 파일'에서 제거`` 가 나온다. 거기 보이는 것은
심볼릭 링크라 Qt 의 "삭제"도 원본을 건드리지는 않지만, 이름만 보고 원본이
지워진다고 읽히기 쉽다. **무엇에서 빠지는지**를 메뉴 이름에 적어 둔다.

**사이드바**(왼쪽)에서 분류를 우클릭하면 정리 메뉴가 나온다.

- 즐겨찾기 분류 → "'설계' 즐겨찾기에서 삭제" (분류 폴더째 제거)
- 최근 파일     → "'최근 파일' 목록 비우기" (항목은 남기고 안만 비움)
- 그 밖의 항목 → "사이드바에서 제거" (Qt 기본 "Remove" 와 같은 동작.
  경로가 없는 "Computer" 항목은 Qt 와 마찬가지로 비활성이고,
  ``fixed_sidebar_urls`` 로 지정한 위치 — 기본은 사용자 홈 — 도 뺄 수 없다)

네이티브 다이얼로그에는 걸 수 없다(내부 위젯이 없다). :meth:`install` 이
False 를 돌려주면 아무 일도 하지 않은 것이다.
"""

import os

from qtpy.QtCore import QFileInfo, QObject, Qt, Signal
from qtpy.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QListView,
    QMenu,
    QMessageBox,
    QTreeView,
)

try:                                    # Qt6 는 QtGui, Qt5 는 QtWidgets
    from qtpy.QtGui import QAction
except ImportError:                     # pragma: no cover
    from qtpy.QtWidgets import QAction

from .constants import PATH_ROLE
from .util import exec_menu, url_path

URL_ROLE = FILE_PATH_ROLE = PATH_ROLE

# 파일 목록 우클릭 메뉴에서 쓰는 Qt 기본 액션들 (objectName)
QT_ITEM_ACTIONS = ("qt_rename_action", "qt_delete_action")
QT_COMMON_ACTIONS = ("qt_show_hidden_action", "qt_new_folder_action")


class FavoritesMenus(QObject):
    """QFileDialog 에 즐겨찾기 관련 우클릭 메뉴를 붙인다.

    사용 예::

        menus = FavoritesMenus(dialog, [favorites, recent])
        menus.install()
        dialog.exec_()

    Args:
        dialog: 대상 :class:`QFileDialog` (``DontUseNativeDialog`` 여야 한다).
        places: :class:`~custom_file_dialog.places.Places` — 즐겨찾기·최근 파일과
            보호 위치를 담은 묶음.
        confirm: 삭제/비우기 전에 확인 대화상자를 띄울지.
        add_menu: 파일 목록에 "즐겨찾기에 추가" 메뉴를 붙일지.

    시그널:
        favoriteAdded(str, str): (분류, 등록한 경로)
        entryRemoved(str, str): (분류, 뺀 항목의 **원본** 경로)
        categoryRemoved(str): 메뉴로 분류를 삭제했을 때 분류 이름.
        recentCleared(str): 메뉴로 최근 목록을 비웠을 때 항목 이름.
        sidebarEntryRemoved(str): 사이드바에서 일반 항목을 뺐을 때 그 경로.
    """

    favoriteAdded = Signal(str, str)
    entryRemoved = Signal(str, str)
    categoryRemoved = Signal(str)
    recentCleared = Signal(str)
    sidebarEntryRemoved = Signal(str)

    def __init__(self, dialog, places, confirm=True, add_menu=True, parent=None):
        # 다이얼로그를 부모로 삼아 다이얼로그가 사는 동안 함께 살아 있게 한다
        super().__init__(parent if parent is not None else dialog)
        self._dialog = dialog
        self._places = places
        self._confirm = confirm
        self._add_menu = add_menu
        self._sidebar = None
        self._views = []

    # ------------------------------------------------------------- 설치
    def install(self):
        """메뉴를 건다. 걸지 못하면 False."""
        if not self._places.stores():
            return False

        self._sidebar = self._dialog.findChild(QListView, "sidebar")
        self._views = [
            view
            for view in (
                self._dialog.findChild(QTreeView, "treeView"),
                self._dialog.findChild(QListView, "listView"),
            )
            if view is not None
        ]
        if self._sidebar is None and not self._views:
            return False        # 네이티브 다이얼로그 등 -> 손댈 수 없다

        if self._sidebar is not None:
            self._take_over(self._sidebar, self._show_sidebar_menu)
        if self._add_menu:
            # 즐겨찾기가 없어도(최근 파일만 써도) "…에서 제거"는 필요하다
            for view in self._views:
                self._take_over(view, self._make_view_handler(view))
        return True

    @staticmethod
    def _take_over(widget, handler):
        """Qt 기본 컨텍스트 메뉴를 떼고 우리 메뉴만 남긴다."""
        try:
            widget.customContextMenuRequested.disconnect()
        except (TypeError, RuntimeError):
            pass
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(handler)

    # ------------------------------------------------------------- 조회
    def is_fixed(self, path):
        """"사이드바에서 제거"가 막힌 위치인지(기본: 사용자 홈)."""
        return self._places.is_fixed(path)

    def fixed_sidebar_urls(self):
        """제거가 막힌 위치 목록(정렬된 경로)."""
        return self._places.fixed_urls()

    # ------------------------------------------------- 파일 목록 우클릭 메뉴
    def _make_view_handler(self, view):
        def handler(pos):
            self._show_view_menu(view, pos)

        return handler

    def path_at(self, view, index):
        """파일 목록 인덱스가 가리키는 경로(없으면 None)."""
        if not index.isValid():
            return None
        path = index.data(FILE_PATH_ROLE)
        if path:
            return str(path)
        # 모델이 경로를 안 주면 현재 폴더 + 이름으로 만든다
        name = index.data()
        if not name:
            return None
        return os.path.join(self._dialog.directory().absolutePath(), str(name))

    def entry_at(self, view, index):
        """파일 목록 인덱스가 가리키는 **분류 안의 항목**.

        Returns:
            ``(저장소, 분류이름, 링크경로)``. 분류 폴더 바로 아래의 항목이
            아니면 ``(None, None, None)``.
        """
        path = self.path_at(view, index)
        if not path:
            return None, None, None
        store = self._places.store_holding(path)
        if store is None:
            return None, None, None
        directory = os.path.dirname(os.path.normpath(path))
        if not store.is_category_dir(directory):
            return None, None, None     # 분류 폴더 **바로 아래**만 (더 깊은 곳은 원본 쪽)
        return store, os.path.basename(directory), path

    def _show_view_menu(self, view, pos):
        menu = self.build_view_menu(view, view.indexAt(pos))
        if menu is None:
            return
        exec_menu(menu, view.viewport().mapToGlobal(pos))

    def build_view_menu(self, view, index):
        """파일 목록 우클릭 메뉴를 만든다(띄우지는 않는다).

        Returns:
            :class:`QMenu`. 붙일 항목이 하나도 없으면 None.
        """
        path = self.path_at(view, index)
        store, category, link = self.entry_at(view, index)
        menu = QMenu(view)

        if store is not None:
            # 1a) 분류 안의 항목 -> "무엇에서" 빠지는지 이름에 적어 준다.
            #     Qt 기본 "삭제"는 같은 일을 하지만 원본을 지우는 것처럼 읽힌다.
            action = menu.addAction("'%s'에서 제거" % category)
            action.triggered.connect(
                lambda _checked=False, s=store, c=category, p=link: self.remove_entry(
                    s, c, p
                )
            )
            menu.addSeparator()
        elif path and self._places.favorites_store() is not None:
            # 1b) 그 밖의 파일·폴더 -> 즐겨찾기에 추가
            self._add_favorite_submenu(menu, path)
            menu.addSeparator()

        # 2) Qt 기본 항목 (이름 변경 · 삭제 · 숨김 파일 · 새 폴더)
        self._add_default_actions(menu, path, allow_delete=store is None)
        return None if menu.isEmpty() else menu

    def _add_favorite_submenu(self, menu, path):
        store = self._places.favorites_store()
        # addMenu("문자열") 이 돌려주는 QMenu 는 PySide6 에서 파이썬 쪽 참조가
        # 남지 않아 곧바로 수거될 수 있다. 부모를 지정해 직접 만들어 붙인다.
        submenu = QMenu("즐겨찾기에 추가", menu)
        menu.addMenu(submenu)
        for category in store.categories():
            action = submenu.addAction(category)
            action.setEnabled(not store.contains(category, path))
            action.triggered.connect(
                lambda _checked=False, c=category: self.add_to_favorites(path, c)
            )
        if store.categories():
            submenu.addSeparator()
        submenu.addAction(
            "새 분류...", lambda: self.add_to_favorites(path, None)
        )

    def _add_default_actions(self, menu, path, allow_delete=True):
        """Qt 가 원래 띄우던 항목들을 그대로 붙인다(활성 상태도 같은 규칙).

        ``allow_delete`` 가 False 면 "삭제"만 뺀다 — 우리가 "…에서 제거"로
        갈아 끼운 자리라 둘을 함께 두면 어느 쪽이 원본을 지우는지 헷갈린다.
        """
        dialog = self._dialog
        read_only = bool(dialog.options() & QFileDialog.Option.ReadOnly)

        if path:
            writable = QFileInfo(path).isWritable()
            for name in QT_ITEM_ACTIONS:
                if name == "qt_delete_action" and not allow_delete:
                    continue
                action = dialog.findChild(QAction, name)
                if action is not None:
                    action.setEnabled(not read_only and writable)
                    menu.addAction(action)
            if not menu.isEmpty():
                menu.addSeparator()

        for name in QT_COMMON_ACTIONS:
            action = dialog.findChild(QAction, name)
            if action is not None and action.isVisible():
                menu.addAction(action)

    def add_to_favorites(self, path, category=None):
        """경로를 즐겨찾기에 등록한다. ``category`` 가 None 이면 새 분류를 묻는다."""
        store = self._places.favorites_store()
        if store is None or not path:
            return False

        if category is None:
            category, ok = QInputDialog.getText(
                self._dialog, "새 분류", "분류 이름:"
            )
            if not ok or not category.strip():
                return False
            category = category.strip()

        from .favorites import FavoritesError

        try:
            store.add(category, path)
        except (FavoritesError, ValueError) as exc:
            QMessageBox.warning(self._dialog, "즐겨찾기 추가 실패", str(exc))
            return False

        self._refresh_sidebar(store)
        self.favoriteAdded.emit(category, path)
        return True

    def _refresh_sidebar(self, store):
        """새로 생긴 분류가 사이드바에 바로 보이도록 항목을 다시 넣는다."""
        existing = list(self._dialog.sidebarUrls())
        known = {os.path.normpath(url_path(url)) for url in existing}
        added = [
            url for url in store.sidebar_urls()
            if os.path.normpath(url_path(url)) not in known
        ]
        if added:
            self._dialog.setSidebarUrls(existing + added)

    # --------------------------------------------------- 사이드바 우클릭 메뉴
    def store_at(self, index):
        """사이드바 인덱스의 ``(저장소, 분류이름)`` (분류가 아니면 ``(None, None)``)."""
        if not index.isValid():
            return None, None
        path = url_path(index.data(URL_ROLE))
        if not path:
            return None, None
        store = self._places.category_store(path)
        if store is None:
            return None, None
        return store, os.path.basename(os.path.normpath(path))

    def category_at(self, index):
        """사이드바 인덱스가 가리키는 분류 이름(분류가 아니면 None)."""
        return self.store_at(index)[1]

    def _show_sidebar_menu(self, pos):
        index = self._sidebar.indexAt(pos)
        if not index.isValid():
            return              # 빈 영역에는 메뉴 없음

        store, category = self.store_at(index)
        menu = QMenu(self._sidebar)

        if store is None:
            # 분류가 아닌 항목(사용자가 끌어다 놓은 폴더, 홈 …)
            # -> Qt 기본 "Remove" 와 같은 동작
            action = menu.addAction("사이드바에서 제거")
            local = url_path(index.data(URL_ROLE))
            # "Computer"(경로 없음)와 보호 위치(기본: 홈)는 뺄 수 없다
            action.setEnabled(bool(local) and not self.is_fixed(local))
            action.triggered.connect(
                lambda _checked=False, p=local: self.remove_sidebar_entry(p)
            )
        elif self._places.is_recent(store):
            action = menu.addAction("'%s' 목록 비우기" % category)
            action.triggered.connect(
                lambda _checked=False: self.clear_recent(store, category)
            )
        else:
            action = menu.addAction("'%s' 즐겨찾기에서 삭제" % category)
            action.triggered.connect(
                lambda _checked=False: self.remove_category(category)
            )

        exec_menu(menu, self._sidebar.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------- 동작
    def remove_sidebar_entry(self, path):
        """사이드바에서 일반 항목(분류가 아닌 폴더)을 뺀다.

        Qt 기본 "Remove" 와 같은 동작이다. 폴더 자체는 지우지 않고 목록에서만
        빠지며, 다이얼로그를 닫을 때 사용자 설정에 저장된다.
        보호 위치(기본: 사용자 홈)는 빼지 않고 False 를 돌려준다.
        """
        if not path or self.is_fixed(path):
            return False
        wanted = os.path.normpath(path)
        remaining = [
            url
            for url in self._dialog.sidebarUrls()
            if os.path.normpath(url_path(url)) != wanted
        ]
        if len(remaining) == len(self._dialog.sidebarUrls()):
            return False
        self._dialog.setSidebarUrls(remaining)
        self.sidebarEntryRemoved.emit(path)
        return True

    def remove_entry(self, store, category, link):
        """분류에서 항목 **하나**를 뺀다. 원본 파일은 건드리지 않는다.

        분류 폴더에 있는 것은 원본을 가리키는 심볼릭 링크라, 그 링크만 지운다.
        목록에 보이는 **이름**으로 지우므로 같은 파일을 다른 이름으로 두 번
        등록해 두었어도 우클릭한 그 항목만 빠진다.

        되돌리기 쉬운(다시 등록하면 그만인) 동작이라 확인 대화상자를 띄우지
        않는다. 분류를 통째로 지우는 :meth:`remove_category` 는 확인한다.
        """
        if store is None or not link:
            return False
        name = os.path.basename(os.path.normpath(link))
        target = store.resolve(link) or link      # 지우기 전에 원본을 알아 둔다
        if not store.remove(category, name):
            return False
        self.entryRemoved.emit(category, target)
        return True

    def remove_category(self, category):
        """분류를 삭제하고 사이드바를 갱신한다(원본 파일은 건드리지 않는다)."""
        if self._confirm and not self._ask(
            "즐겨찾기 삭제",
            "'%s' 분류를 즐겨찾기에서 삭제할까요?\n"
            "등록된 원본 파일은 삭제되지 않습니다." % category,
        ):
            return False

        store = self._places.store_for_category(category)
        if store is None:
            return False
        directory = store.category_dir(category)
        store.remove_category(category)

        remaining = [
            url
            for url in self._dialog.sidebarUrls()
            if os.path.normpath(url_path(url)) != os.path.normpath(directory)
        ]
        self._dialog.setSidebarUrls(remaining)

        if self._is_current(directory):
            self._dialog.setDirectory(os.path.expanduser("~"))

        self.categoryRemoved.emit(category)
        return True

    def clear_recent(self, store, category=None):
        """최근 파일 목록을 비운다(사이드바 항목은 남긴다)."""
        name = category or getattr(store, "name", "최근 파일")
        if self._confirm and not self._ask(
            "최근 파일 비우기",
            "'%s' 목록을 비울까요?\n"
            "목록만 지워지고 원본 파일은 그대로 남습니다." % name,
        ):
            return False

        store.clear()
        directory = store.category_dir(name)
        if self._is_current(directory):
            self._dialog.setDirectory(directory)

        self.recentCleared.emit(name)
        return True

    def _is_current(self, directory):
        current = os.path.normpath(self._dialog.directory().absolutePath())
        return current == os.path.normpath(directory)

    def _ask(self, title, text):
        answer = QMessageBox.question(
            self._dialog,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
