"""즐겨찾기·최근 파일의 **심볼릭 링크**를 원본처럼 다루게 한다.

분류 폴더에 보이는 것은 원본을 가리키는 링크라, 그대로 두면 다이얼로그가 링크
창고 안을 헤매게 된다. 세 자리에서 원본을 따라가게 손본다.

- :func:`show_link_target_in_combo` — 항목을 고르면 "Look in" 에 원본 경로
- :func:`follow_link_directories` — 링크 폴더로 들어가면 원본 폴더로
- :func:`follow_link_on_parent` — "상위 폴더(↑)" 도 원본 기준으로

Qt 가 C++ 에서 연결해 둔 슬롯은 파이썬에서 끊을 수 없다. 그래서 **신호가 나기
전 단계를 보고 목적지를 정해 두었다가 Qt 가 옮긴 직후에 바로잡는** 방식을 쓴다.
"""

import os
import weakref

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QComboBox, QToolButton

from . import safety
from .util import abspath


def show_link_target_in_combo(dialog, places):
    """항목을 고르면 "Look in" 콤보에 그 항목의 **실제 위치**를 보여 준다.

    **폴더를 옮기지는 않는다.** 표시만 바꾸므로 목록은 분류에 그대로 머문다.
    폴더를 이동하면 Qt 가 콤보를 다시 채우므로 표시도 저절로 되돌아온다.
    """
    combo = dialog.findChild(QComboBox, "lookInCombo")
    if combo is None:
        return False

    def on_current_changed(path):
        index = combo.currentIndex()
        if index >= 0:
            # 링크가 아니면 현재 폴더 경로로 되돌린다(앞서 바꿔 놨을 수 있으므로)
            combo.setItemText(
                index, places.link_target(path) or dialog.directory().absolutePath()
            )

    dialog.currentChanged.connect(on_current_changed)
    return True


def follow_link_on_parent(dialog, places):
    """분류 폴더에서 링크를 고르고 **"상위 폴더"** 를 누르면 원본 위치로 올라간다.

    Qt 는 지금 보고 있는 폴더(=분류 폴더)의 부모로 올라간다. 그래서 링크를 모아 둔
    저장소 안쪽으로 들어가 버리는데, 거기는 분류 폴더만 늘어선 창고라 열어 봐야
    쓸 것이 없다. "Look in" 이 이미 가리키고 있는 **원본 경로**를 기준으로 올라가,
    고른 것이 실제로 있는 폴더로 가게 바로잡는다::

        <favorites>/설계/data.csv   (원본 /mnt/proj/설계문서/data.csv)
        "상위 폴더" ->  /mnt/proj/설계문서      (전에는 <favorites>)

    폴더 링크도 같은 규칙이다 — 원본이 **있는** 폴더로 간다.

    Qt 가 C++ 에서 연결해 둔 ``_q_navigateToParent`` 는 파이썬에서 끊을 수 없다.
    그래서 버튼이 눌린 순간에 목적지를 미리 정해 두었다가, Qt 가 옮긴 **직후에
    바로잡는다**. 잘못 들르는 저장소는 늘 로컬이라 오가는 비용이 없다.
    (Alt+Up 단축키도 이 버튼에 걸려 있어 함께 처리된다.)
    """
    button = dialog.findChild(QToolButton, "toParentButton")
    if button is None:
        return False

    state = {"target": None, "goto": None}

    # 다이얼로그를 약한 참조로 잡는다. 이 훅만 **자식 위젯**(상위 폴더 버튼)의
    # 시그널에 연결하는데, 클로저가 다이얼로그를 직접 들고 있으면
    # 다이얼로그 -> 버튼 -> 연결 -> 클로저 -> 다이얼로그 순환이 생기고, 그
    # 고리에 C++ 객체가 끼어 파이썬 gc 가 풀지 못한다(다이얼로그를 반복해서
    # 여는 앱에서 그대로 쌓인다 — 실측으로 확인).
    dialog_ref = weakref.ref(dialog)

    def on_current_changed(path):
        state["target"] = places.link_target(path)

    def disarm():
        state["goto"] = None

    def on_pressed():
        # 누른 그 순간의 상황으로 목적지를 정한다(누른 뒤에는 이미 옮겨진 뒤다)
        state["goto"] = None
        target = state["target"]
        owner = dialog_ref()
        if not target or owner is None:
            return
        if not places.is_inside(owner.directory().absolutePath()):
            return                       # 분류 폴더 안에서 고른 링크일 때만
        state["goto"] = os.path.dirname(abspath(target) or "")
        # 진짜 클릭이면 이동과 directoryEntered 가 이 이벤트 처리 안에서 동기로
        # 끝난다(타이머는 그 뒤에 돈다). 눌렀다가 끌어서 밖에서 놓으면 이동이
        # 없는데, 장전만 된 채 남으면 **다음 평범한 이동을 가로채** 엉뚱한 곳으로
        # 보내 버린다. 이벤트 루프로 돌아오면 무장을 푼다.
        QTimer.singleShot(0, disarm)

    def on_entered(_path):
        destination, state["goto"], state["target"] = state["goto"], None, None
        owner = dialog_ref()
        if not destination or owner is None:
            return
        # 형제 함수(follow_link_directories)와 같은 기준을 쓴다. safe_isdir 만
        # 보면 min_depth 로 막아 둔 얕은 자리로 옮겨 갈 수 있다 — setDirectory 는
        # directoryEntered 를 내지 않아 마지막 방어도 안 걸린다.
        if not safety.may_enter(destination):
            return
        # 원본이 죽은 마운트에 있으면 그냥 둔다(GUI 를 멈추느니 제자리가 낫다)
        if safety.safe_isdir(destination):
            owner.setDirectory(destination)

    dialog.currentChanged.connect(on_current_changed)
    button.pressed.connect(on_pressed)
    dialog.directoryEntered.connect(on_entered)
    return True


def follow_link_directories(dialog, places):
    """링크 폴더로 들어가면 원본 위치로 옮긴다.

    Qt 는 표시 경로에서 심볼릭 링크를 풀어 주지 않으므로, 그런 폴더에 들어간
    순간 원본으로 다시 보내 그 뒤로는 평범한 실제 경로로 탐색이 이어지게 한다.
    """

    def on_entered(path):
        target = places.link_target(path)
        if target is None:
            return
        # 원본이 열면 안 되는 자리이거나(얕은 자리·automount·차단 경로) 죽은
        # 마운트면 그대로 둔다. setDirectory 는 그 폴더를 통째로 나열하고,
        # directoryEntered 를 다시 내지 않아 마지막 방어도 걸리지 않는다.
        if not safety.may_enter(target) or not safety.safe_isdir(target):
            return
        dialog.setDirectory(target)

    dialog.directoryEntered.connect(on_entered)
    return True


