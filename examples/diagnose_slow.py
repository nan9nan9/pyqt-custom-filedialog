"""사이드바가 왜 느린지 **그 환경에서** 찍어 본다.

    python examples/diagnose_slow.py

데모와 같은 구성으로 다이얼로그를 열고, 사이드바 항목을 하나씩 눌러 가며
**어느 단계에서 시간이 가는지** 나눠 잰다. 창은 실제로 뜨며(오프스크린이 아님),
조작은 프로그램이 대신 한다.

클릭 한 번의 시간을 **우리 코드**와 **Qt** 로 갈라 잰다.

    우리 코드   이 패키지 안에서 쓴 시간(판정 · 훅 · 아이콘 제공자)
    Qt         나머지 — 그 폴더로 옮기고 **나열**하는 데 Qt 가 쓴 시간

Qt 쪽이 대부분이면 그 폴더를 읽는 것 자체가 느린 것이고(네트워크 지연 ·
파일 수), 우리 코드 쪽이 크면 어느 함수인지까지 찍어 준다.

데모의 "안전장치" 체크박스와 같은 설정으로 재려면 ``--safety`` 를 붙인다.
"""

import cProfile
import os
import pstats
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qtpy.QtCore import Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QListView

from custom_file_dialog import CustomFileDialog, FavoritesStore, RecentStore, safety
from custom_file_dialog import mounts, reach


def _mount_report():
    """마운트 표에서 이 환경의 성격을 요약한다."""
    table = mounts.iter_mounts(refresh=True)
    remote = [m for m in table if m[1] in mounts.REMOTE_FSTYPES]
    auto = [m for m in table if m[1] in mounts.AUTOMOUNT_FSTYPES]
    print("마운트 표: 전체 %d개 · 원격 %d개 · automount %d개"
          % (len(table), len(remote), len(auto)))
    options = _mount_options()
    for point, fstype, source in (remote + auto)[:12]:
        print("    %-36s %-9s %s" % (point, fstype, source))
        flags = options.get(os.path.normpath(point), "")
        caching = [f for f in flags.split(",")
                   if f.startswith(("ac", "noac", "actimeo", "lookupcache", "vers", "proto"))]
        if caching:
            print("        옵션: %s" % ",".join(caching))
        if "noac" in flags.split(",") or "actimeo=0" in flags:
            print("        ** 속성 캐시가 꺼져 있다(noac/actimeo=0). 파일 정보를"
                  " 물을 때마다 서버까지 간다 — 목록이 느린 가장 흔한 원인이다. **")
    if len(remote) + len(auto) > 12:
        print("    … 외 %d개" % (len(remote) + len(auto) - 12))
    return table


def _mount_options():
    """``마운트지점 -> 옵션문자열``. mountinfo 의 옵션 칸을 그대로 읽는다."""
    found = {}
    try:
        with open(mounts.MOUNTINFO, encoding="utf-8", errors="surrogateescape") as handle:
            lines = handle.readlines()
    except OSError:
        return found
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 4:
            continue
        point = os.path.normpath(fields[4])
        found[point] = fields[5] + "," + fields[separator + 3]
    return found


def _listing_cost(directory):
    """``ls`` 와 Qt 가 왜 다른지 — 같은 폴더로 직접 재 본다.

    ``ls`` 는 폴더만 읽고(getdents 한 번), Qt 는 크기·종류·시각을 채우려고
    **항목마다 stat** 한다. 네트워크에서는 그 stat 하나하나가 서버까지 가는
    왕복이라, 폴더 읽기가 아무리 빨라도 목록은 느리다.
    """
    started = time.perf_counter()
    try:
        names = os.listdir(directory)
    except OSError as error:
        print("    폴더를 읽지 못했다: %s" % error)
        return
    read = time.perf_counter() - started

    started = time.perf_counter()
    for name in names:
        try:
            os.lstat(os.path.join(directory, name))
        except OSError:
            pass
    stat_all = time.perf_counter() - started

    print("    항목 %d개 · 폴더만 읽기(ls 가 하는 일) %.0f ms"
          " · 항목마다 stat(Qt 가 하는 일) %.0f ms"
          % (len(names), read * 1000, stat_all * 1000))
    if names and stat_all > 4 * max(read, 0.0005):
        print("        -> stat 이 %.0f배 비싸다. 목록이 느린 것은 폴더 크기가"
              " 아니라 **항목마다 서버에 묻는 비용** 이다." % (stat_all / max(read, 1e-6)))


def _where(path, table):
    """그 경로가 어느 마운트에 얹혀 있는지 한 줄로."""
    mount = mounts.mount_for(path)
    if mount is None:
        return "마운트 모름"
    kind = "원격" if mount[1] in mounts.REMOTE_FSTYPES else (
        "automount" if mount[1] in mounts.AUTOMOUNT_FSTYPES else "로컬")
    return "%s (%s, %s)" % (kind, mount[1], mount[0])


def _timed(func):
    started = time.perf_counter()
    return func(), time.perf_counter() - started


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    table = _mount_report()

    home = os.path.expanduser("~")
    storage = os.path.join(home, ".config", "custom-file-dialog-진단")
    work = tempfile.mkdtemp(prefix="cfd-진단-")
    for index in range(5):
        with open(os.path.join(work, "파일%02d.csv" % index), "w") as handle:
            handle.write("x")

    print("\n홈        %-60s %s" % (home, _where(home, table)))
    print("저장소    %-60s %s" % (storage, _where(storage, table)))
    print("작업폴더  %-60s %s" % (work, _where(work, table)))

    favorites = FavoritesStore(base_dir=os.path.join(storage, "favorites"))
    recent = RecentStore(base_dir=os.path.join(storage, "recent"), max_items=10)
    favorites.add("설계", os.path.join(work, "파일00.csv"))
    recent.record(os.path.join(work, "파일01.csv"))

    if "--safety" in sys.argv:
        # 데모의 "안전장치" 체크박스와 같은 설정
        safety.configure(guarded_roots=[os.path.join(work, "user")], min_depth=2)
    print("\n안전 설정: %s" % safety.settings())

    dialog, spent = _timed(lambda: CustomFileDialog(
        None, mode="open_file", directory=work, favorites=favorites, recent=recent
    ))
    dialog.show()
    for _ in range(40):
        app.processEvents()
    print("다이얼로그 생성 %.0f ms (멈춘 확인 스레드 %d개)"
          % (spent * 1000, reach.pending_checks()))

    sidebar = dialog.findChild(QListView, "sidebar")
    if sidebar is None:
        print("사이드바를 못 찾았다 — 네이티브 창인 것 같다.")
        return

    rows = sidebar.model().rowCount()
    names = [sidebar.model().index(r, 0).data() for r in range(rows)]
    print("\n사이드바 %d개: %s" % (rows, names))

    print("\n사이드바가 가리키는 폴더를 직접 재 본다 (ls vs Qt 의 차이):")
    from custom_file_dialog.constants import PATH_ROLE
    from custom_file_dialog.util import url_path
    for row in range(rows):
        target = url_path(sidebar.model().index(row, 0).data(PATH_ROLE))
        if target and os.path.isdir(target):
            print("  %s  (%s)" % (names[row], target))
            _listing_cost(target)

    print("\n%-18s %10s %12s %12s   %s"
          % ("항목", "전체", "우리 코드", "Qt", "간 곳"))
    worst = []
    for round_index in range(2):         # 두 바퀴 — 첫 바퀴는 캐시가 비어 있다
        if round_index:
            print("  -- 두 번째 바퀴 (캐시가 채워진 뒤) --")
        for row in range(rows):
            index = sidebar.model().index(row, 0)
            point = sidebar.visualRect(index).center()

            profile = cProfile.Profile()
            started = time.perf_counter()
            profile.enable()
            QTest.mousePress(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseRelease(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=point)
            for _ in range(60):
                app.processEvents()
            profile.disable()
            total = time.perf_counter() - started

            ours, rows_by_time = _our_share(profile)
            worst.extend(rows_by_time)
            print("%-18s %8.0f ms %10.1f ms %10.0f ms   %s"
                  % (names[row][:18], total * 1000, ours * 1000,
                     (total - ours) * 1000, dialog.directory().absolutePath()))

    print("\n멈춘 확인 스레드: %d개" % reach.pending_checks())

    worst.sort(reverse=True)
    if worst and worst[0][0] > 0.001:
        print("\n우리 코드에서 오래 걸린 함수:")
        for spent, where in worst[:8]:
            print("    %7.1f ms  %s" % (spent * 1000, where))

    print("\n읽는 법:")
    print("  · 'Qt' 가 대부분이면 -> 위의 'ls vs Qt' 줄을 보라. Qt 는 크기·종류·")
    print("    시각을 채우려고 **항목마다 stat** 한다(실측: 항목당 10회 이상,")
    print("    오갈 때마다 다시). ls 는 폴더만 읽으므로 빠른 것이 정상이고,")
    print("    공정한 비교는 `ls -lU` 다. 네트워크에서는 이 stat 이 전부 왕복이다.")
    print("  · '우리 코드' 가 크면 -> 위에 찍힌 함수가 범인이다. 그 줄을 알려 달라.")
    print("  · 어떤 경로를 몇 번 만지는지까지 보려면:")
    print("      strace -f -e trace=%file -o /tmp/cfd.trace \\")
    print("          %s %s" % (sys.executable, os.path.abspath(__file__)))
    print("      awk -F'\"' '{print $2}' /tmp/cfd.trace | sort | uniq -c | sort -rn | head -20")

    dialog.done(0)


def _our_share(profile):
    """프로파일에서 **이 패키지 코드**가 쓴 시간만 뽑는다."""
    stats = pstats.Stats(profile)
    total = 0.0
    rows = []
    for (filename, lineno, name), entry in stats.stats.items():
        if "custom_file_dialog" not in filename:
            continue
        tottime = entry[2]
        total += tottime
        rows.append((tottime, "%s:%d %s" % (os.path.basename(filename), lineno, name)))
    return total, rows


if __name__ == "__main__":
    main()
