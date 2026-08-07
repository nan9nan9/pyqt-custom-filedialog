"""FilePathForm: 여러 개의 경로 선택 줄을 라벨 정렬해서 담는 폼 위젯.

입력 파일 · 출력 폴더 · 설정 파일처럼 경로를 여러 개 받아야 하는 화면에서,
:class:`~file_dialog_widget.path_edit.FilePathEdit` 를 하나씩 배치하는 대신
키(key)로 값을 꺼내 쓸 수 있게 묶어 준다.
"""

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QFormLayout, QWidget

from .constants import is_multi_mode
from .path_edit import FilePathEdit


class FilePathForm(QWidget):
    """key -> :class:`FilePathEdit` 매핑을 갖는 QFormLayout 기반 폼.

    사용 예::

        form = FilePathForm()
        form.add_path("input", "입력 파일:", mode="open_file",
                      filters=[("CSV", ["csv"])], required=True)
        form.add_path("outdir", "출력 폴더:", mode="directory")

        form.values()       # {"input": "...", "outdir": "..."}
        form.is_valid()     # 모든 줄이 유효한가

    시그널:
        valueChanged(str, str): (key, 경로) — 어느 줄이든 값이 바뀌면.
        validityChanged(bool): 폼 전체의 유효 여부가 바뀌면.
    """

    valueChanged = Signal(str, str)
    validityChanged = Signal(bool)

    def __init__(self, parent=None, spacing=6, margins=(0, 0, 0, 0)):
        super().__init__(parent)
        self._edits = {}
        self._order = []
        self._valid = True

        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)
        # 라벨이 길어도 줄바꿈되지 않고 입력창이 늘어나도록.
        self._layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

    # ---------------------------------------------------------------- 구성
    def add_path(self, key, label, **kwargs):
        """경로 선택 줄을 추가하고 만들어진 :class:`FilePathEdit` 를 반환한다.

        Args:
            key: 값을 꺼낼 때 쓸 이름. 중복되면 ValueError.
            label: 왼쪽에 표시할 라벨 텍스트.
            **kwargs: 그대로 :class:`FilePathEdit` 생성자로 전달된다
                (mode, filters, required, history, settings_key 등).
        """
        if key in self._edits:
            raise ValueError("이미 등록된 key 입니다: %r" % key)

        # 라벨은 QFormLayout 이 정렬해 주므로 위젯 자체에는 붙이지 않는다.
        kwargs.pop("label", None)
        edit = FilePathEdit(parent=self, **kwargs)
        self._edits[key] = edit
        self._order.append(key)

        edit.pathChanged.connect(lambda path, k=key: self._on_path_changed(k, path))
        edit.validityChanged.connect(lambda _ok: self._update_validity())

        self._layout.addRow(label, edit)
        self._update_validity()
        return edit

    def add_row(self, label, widget):
        """경로 줄이 아닌 임의의 위젯을 같은 폼에 추가한다(정렬 유지)."""
        self._layout.addRow(label, widget)
        return widget

    # ---------------------------------------------------------------- 접근
    def edit(self, key):
        """key 에 해당하는 :class:`FilePathEdit` (없으면 None)."""
        return self._edits.get(key)

    def keys(self):
        """추가한 순서대로의 key 목록."""
        return list(self._order)

    def value(self, key):
        """해당 줄의 경로 문자열 (없으면 빈 문자열)."""
        edit = self._edits.get(key)
        return edit.path() if edit is not None else ""

    def values(self):
        """``{key: 경로}`` 딕셔너리. open_files 줄은 리스트로 담긴다."""
        result = {}
        for key in self._order:
            edit = self._edits[key]
            paths = edit.paths()
            if is_multi_mode(edit.mode()):
                result[key] = paths
            else:
                result[key] = paths[0] if paths else ""
        return result

    def set_values(self, values):
        """``{key: 경로 또는 경로 리스트}`` 로 여러 줄을 한 번에 채운다."""
        for key, value in (values or {}).items():
            edit = self._edits.get(key)
            if edit is None:
                continue
            if isinstance(value, (list, tuple)):
                edit.set_paths(list(value))
            else:
                edit.set_path(value)

    def clear(self):
        """모든 줄을 비운다."""
        for edit in self._edits.values():
            edit.clear()

    # -------------------------------------------------------------- 유효성
    def is_valid(self):
        """모든 줄이 유효한지 여부."""
        return self._valid

    def invalid_items(self):
        """``[(key, 사유)]`` 형태로 유효하지 않은 줄들을 반환한다."""
        return [
            (key, self._edits[key].invalid_reason())
            for key in self._order
            if not self._edits[key].is_valid()
        ]

    def _on_path_changed(self, key, path):
        self.valueChanged.emit(key, path)

    def _update_validity(self):
        valid = all(edit.is_valid() for edit in self._edits.values())
        if valid != self._valid:
            self._valid = valid
            self.validityChanged.emit(valid)
