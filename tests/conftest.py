"""공용 부트스트랩 — 모든 테스트 모듈보다 먼저 import 된다.

QApplication 이 필요하므로 QT_QPA_PLATFORM=offscreen 으로 돌리고,
Qt 가 사이드바/히스토리를 사용자 설정에 영구 저장하므로 QSettings 저장 위치를
임시 폴더로 돌린다 (QApplication 생성 전, 두 포맷 모두).
"""

import atexit
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from qtpy.QtCore import QSettings  # noqa: E402

_SETTINGS_DIR = tempfile.mkdtemp(prefix="custom-file-dialog-test-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _SETTINGS_DIR)
    QSettings.setPath(_fmt, QSettings.Scope.SystemScope, _SETTINGS_DIR)
atexit.register(shutil.rmtree, _SETTINGS_DIR, True)

from qtpy.QtWidgets import QApplication  # noqa: E402

from custom_file_dialog import FavoritesStore, RecentStore  # noqa: E402
from custom_file_dialog import dialog as dialog_module  # noqa: E402
from custom_file_dialog import safety  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fake_dialog(monkeypatch):
    """다이얼로그를 띄우는 대신 미리 정한 결과를 돌려주도록 바꾼다."""
    calls = []
    result = {"paths": [], "filter": ""}

    def fake(**kwargs):
        calls.append(kwargs)
        return list(result["paths"]), result["filter"]

    monkeypatch.setattr(dialog_module, "exec_file_dialog", fake)
    return {"calls": calls, "result": result}


@pytest.fixture
def dead_nfs(monkeypatch, tmp_path):
    """응답 없는 NFS 마운트를 흉내 낸다.

    실제로 멈추는 마운트를 만들 수 없으므로, 마운트 표와 소켓 프로브,
    그리고 os.stat 을 갈아 끼워 "영원히 안 돌아오는 경로"를 만든다.
    """
    from custom_file_dialog import safety

    mountpoint = str(tmp_path / "nfs")
    os.mkdir(mountpoint)

    safety.clear_cache()
    monkeypatch.setattr(
        safety,
        "iter_mounts",
        lambda refresh=False: [
            ("/", "ext4", "/dev/sda1"),
            (mountpoint, "nfs4", "nfs1.corp:/export/proj"),
        ],
    )

    # probe_ok: 소켓 프로브 결과 / stat_hangs: stat 이 안 돌아오는지 (따로 조절)
    state = {"probe_ok": False, "stat_hangs": True, "probes": [], "stat_calls": 0}

    def fake_probe(host, port, timeout=None):
        state["probes"].append((host, port))
        return state["probe_ok"]

    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path).startswith(mountpoint):
            state["stat_calls"] += 1
            if state["stat_hangs"]:
                time.sleep(1.0)         # 제한 시간(0.2초)보다 훨씬 길게
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(safety, "probe_host", fake_probe)
    monkeypatch.setattr(os, "stat", fake_stat)

    yield {"mount": mountpoint, "state": state, "safety": safety}
    safety.clear_cache()


@pytest.fixture
def guarded_root(tmp_path):
    """마운트가 잔뜩 달린 ``/user`` 같은 자리를 흉내 낸다."""
    from custom_file_dialog import safety

    root = tmp_path / "user"
    root.mkdir()
    for name in ("jekai", "alice", "bob"):
        (root / name).mkdir()
    (root / "jekai" / "proj").mkdir()

    safety.configure(guarded_roots=[str(root)])
    yield str(root)
    safety.configure(guarded_roots=[])
    safety.clear_cache()


@pytest.fixture
def shallow_tree(tmp_path):
    """얕은 자리를 흉내 낸다 — 그 폴더의 깊이를 함께 돌려준다.

    실제 ``/user`` (깊이 1)는 테스트에서 만들 수 없으므로, tmp 폴더의 깊이를
    재서 "이 폴더가 딱 한 단계 모자라는" min_depth 를 걸어 같은 상황을 만든다.
    """
    from custom_file_dialog import safety

    root = tmp_path / "user"
    root.mkdir()
    for name in ("jekai", "jane", "joe"):
        (root / name).mkdir()
    (root / "jekai" / "proj").mkdir()

    yield str(root), safety.path_depth(str(root))
    safety.reset()


@pytest.fixture
def store(tmp_path):
    return FavoritesStore(base_dir=str(tmp_path / "favorites"))


@pytest.fixture
def recent(tmp_path):
    return RecentStore(base_dir=str(tmp_path / "recent"), max_items=3)

