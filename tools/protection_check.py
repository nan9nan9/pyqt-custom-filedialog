"""보호가 정말 잠겨 있는지 확인한다 — 핵심 판정에 일부러 결함을 심어 본다.

    python tools/protection_check.py            전부
    python tools/protection_check.py 아이콘      이름에 그 말이 든 것만

이 라이브러리가 지키는 것들(automount 를 건드리지 않기 · 남의 기록을 지우지
않기 · 죽은 마운트에서 멈추지 않기 …)은 규칙이 여러 통로에 걸쳐 있어서,
테스트가 "통과"해도 무엇을 지키는지 알기 어렵다. **소스에 결함을 하나 심고
테스트가 잡는지** 보면 확실하다. 아래 표의 한 줄이 곧 "예전에 실제로 났던
버그 하나 + 그것을 잡는 테스트" 다.

각 줄은 ``(무엇을 심나, 파일, 원래 코드, 심을 코드, 잡아야 할 테스트)`` 다.
원래 코드가 **그대로** 있어야 심을 수 있으므로, 리팩터링으로 그 줄이 바뀌면
여기서 ``원문 없음`` 으로 알려 준다 — 조용히 넘어가지 않는다. 그때는 새 원문에
맞춰 이 표를 고치면 된다(보호가 사라진 것인지 옮겨진 것인지 함께 확인할 것).
"""

import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "custom_file_dialog"

# (설명, 파일, 원래 코드, 심을 결함, 잡아야 할 테스트)
CASES = [   (   'history 자르는 기준',
        'history.py',
        'del items[max(self.max_items, stored) :]',
        'del items[max(self.max_items, len(items) - 1) :]',
        'keeps_other_widgets_entries'),
    (   'reach UNKNOWN 묶음키',
        'reach.py',
        '            func, path, timeout=timeout, pending_key=_unknown_key(path)\n',
        '            func, path, timeout=timeout\n',
        'unknown_paths_do_not_pile_up'),
    (   'reach UNKNOWN 프로브',
        'reach.py',
        '            os.path.lexists, path, timeout=timeout, pending_key=_unknown_key(path)',
        '            os.stat, path, timeout=timeout, pending_key=_unknown_key(path)',
        'missing_path_is_reachable'),
    (   'dialog ~ 펴기(후보)',
        'dialog.py',
        '    current = os.path.expanduser((current_paths or [""])[0] or "")',
        '    current = (current_paths or [""])[0] or ""',
        'expands_tilde'),
    (   'dialog ~ 펴기(반환)',
        'dialog.py',
        '            return os.path.expanduser(candidate)',
        '            return candidate',
        'expands_tilde'),
    (   'places recent_max',
        'places.py',
        '        recent_max = changes.pop("recent_max", None)\n'
        '        if recent_max is None:\n'
        '            recent_max = self.recent_max',
        '        recent_max = changes.pop("recent_max", self.recent_max)',
        'recent_max_on_explicit_none'),
    (   'menus places 방어',
        'menus.py',
        'if self._dialog is None or self._places is None:',
        'if self._dialog is None:',
        'reject_missing_places'),
    (   'guard 막은 경로 안내',
        'guard.py',
        '        return self._first_blocked(entries) or entries[0][1]',
        '        return entries[0][1]',
        'names_the_path_that_blocked'),
    (   'guard 모드별 따옴표',
        'guard.py',
        '                _typed_path(self._dialog, t) for t in _typed_parts(self._dialog, '
        'text)',
        '                _typed_path(self._dialog, t) for t in _split_typed(text)',
        'quotes_in_names'),
    (   'mounts bytes 해석',
        'mounts.py',
        '    if isinstance(path, bytes):\n        path = os.fsdecode(path)\n',
        '',
        'guarded_root_accepts_bytes'),
    (   'drops 폴더모드 확인',
        'drops.py',
        '    if not isfile(path):\n        return None\n',
        '',
        'invents_a_parent'),
    (   'drops 파일모드 확인',
        'drops.py',
        '    if not isfile(path):',
        '    if False:',
        'unverifiable_folder_in_file_mode'),
    (   'places 소유 저장소 보호',
        'places.py',
        '            and self.recent_is_ours\n',
        '',
        'app_owned_store'),
    (   'menus 새폴더 활성',
        'menus.py',
        '            if name == "qt_new_folder_action":',
        '            if False:',
        'read_only_dialog'),
    (   'favorites 이름 길이',
        'favorites.py',
        '        return _safe_name(_shorten(str(name), _LINK_NAME_BYTES))',
        '        return _safe_name(str(name))',
        'leak_os_errors'),
    (   'favorites 널바이트',
        'favorites.py',
        '    if "\\0" in text:\n        raise ValueError("분류 이름에 널 바이트를 쓸 수 없습니다.")\n',
        '',
        'leak_os_errors'),
    (   'favorites 오류 문구',
        'favorites.py',
        '            if not safety.is_reachable(target):',
        '            if False:',
        'unverifiable_target'),
    (   'reach 묶음키 범위',
        'reach.py',
        '    return os.path.dirname(os.path.abspath(path)) or os.sep',
        '    return os.path.splitdrive(os.path.abspath(path))[0] or os.sep',
        'dead_share_does_not_lock'),
    (   'reach 스레드 상한',
        'reach.py',
        '        if len(_pending) >= MAX_PENDING_CHECKS:\n'
        '            # 묶음 키가 못 잡은 폭주의 마지막 방어. 키를 아무리 잘 잡아도\n'
        '            # "어디까지가 한 마운트인지" 모르는 경우가 있어, 스레드 수 자체에\n'
        '            # 상한을 둔다(멈춘 스레드는 죽일 수 없으므로 안 만드는 수밖에 없다).\n'
        '            return False, None\n',
        '',
        'hard_ceiling'),
    (   'favorites 분류 길이 한도',
        'favorites.py',
        '_MAX_NAME_BYTES = 255',
        '_MAX_NAME_BYTES = 200',
        'existing_long_category'),
    (   'guard 조각별 구분자',
        'guard.py',
        '            if part.strip().endswith(("/", os.sep)) and not '
        'candidate.endswith(os.sep):',
        '            if (self._edit.text() or "").strip().endswith(("/", os.sep)) and not '
        'candidate.endswith(os.sep):',
        'trailing_separator_counts'),
    (   'places 링크 풀기',
        'places.py',
        '            self.recent.record_all(self.resolve_all(paths))',
        '            self.recent.record_all(paths)',
        'record_recent_resolves'),
    (   '예산 공유(프로브)',
        'reach.py',
        '            share = min(_remaining(deadline), probe_budget(wait))',
        '            share = wait',
        'never_spends_more or probe_budget_follows'),
    (   '예산 공유(stat)',
        'reach.py',
        '    left = _remaining(deadline)\n    if left <= 0:\n        return False\n',
        '    left = wait\n',
        'never_spends_more'),
    (   '제공자 한 번만 설치',
        'dialog.py',
        '            self._icon_provider = provider      # setIconProvider 는 소유하지 않는다\n'
        '            self.setIconProvider(provider)',
        '            self._icon_provider = provider\n'
        '            self.setIconProvider(provider)\n'
        '            self.directoryEntered.connect(\n'
        '                lambda _p: self.setIconProvider(provider))',
        'icon_provider_is_never_swapped'),
    (   '아는 답 넘기기',
        'icons.py',
        '                    if store.is_category_dir(path, is_dir=is_dir):',
        '                    if store.is_category_dir(path):',
        'does_not_restat'),
    (   '넘겨받은 답 쓰기',
        'favorites.py',
        '        return os.path.isdir(absolute) if is_dir is None else bool(is_dir)',
        '        return os.path.isdir(absolute)',
        'does_not_restat'),
    (   '종류별 캐시',
        'icons.py',
        '        icon = self._plain_icons.get(key)\n'
        '        if icon is None:\n'
        '            icon = self._plain_icons[key] = super().icon(info)\n'
        '        return icon',
        '        return super().icon(info)',
        'asks_qt_once_per_kind'),
    (   '링크 표시 유지',
        'icons.py',
        'key = (self._kind(info), info.isSymLink(), self._suffix(info))',
        'key = (self._kind(info), self._suffix(info))',
        'symlinks_distinct'),
    (   '뿌리를 폴더와 구분',
        'icons.py',
        '        if info.isRoot():\n            return "root"',
        '        if False:\n            return "root"',
        'matches_qt_for_every_kind'),
    (   '특수 파일을 파일과 구분',
        'icons.py',
        '        if info.isFile():\n            return "file"\n        return "other"',
        '        return "file"',
        'matches_qt_for_every_kind'),
    (   '프로브 예산 비례',
        'reach.py',
        '    return min(wait, max(PROBE_TIMEOUT, wait * PROBE_SHARE))',
        '    return min(wait, PROBE_TIMEOUT)',
        'probe_budget_follows'),
    (   '점파일 묶기',
        'icons.py',
        '        return name[dot + 1:].lower() if dot > 0 else ""',
        '        return info.suffix().lower()',
        'asks_qt_once_per_kind'),
    (   '사이드바 차단 설치',
        'guard.py',
        '_watch(sidebar, _SidebarBlocker(sidebar, parent=dialog), installed)',
        '_watch(sidebar, _ItemBlocker(sidebar, (_RELEASE,), parent=dialog), installed)',
        'sidebar_click_cannot'),
    (   '사이드바 방향키',
        'guard.py',
        '            step = self._STEPS.get(event.key())',
        '            step = None',
        'sidebar_keyboard_move'),
    (   'bounce 미루기',
        'guard.py',
        '        QTimer.singleShot(0, lambda: _set_directory(dialog, last["dir"]))',
        '        _set_directory(dialog, last["dir"])',
        'bounces_back'),
    (   '시작위치 이름칸',
        'dialog.py',
        '        if safety.may_enter(directory):\n'
        '            self.selectFile(os.path.basename(directory))',
        '        self.selectFile(os.path.basename(directory))',
        'blocked_start_dir_does_not_fill'),
    (   '사이드바 목록 반환',
        'path_edit.py',
        '        return list(self._places().sidebar_urls(self._current_dir_now()) or [])',
        '        urls = self._places().sidebar_urls(self._current_dir_now())\n'
        '        return list(urls) if urls is not None else None',
        'effective_sidebar_urls_is_always')]


def run_case(label, filename, good, bad, selector):
    """결함을 심고 테스트를 돌린 뒤 원래대로 되돌린다."""
    path = SRC / filename
    original = path.read_text(encoding="utf-8")
    if good not in original:
        return "원문 없음"
    path.write_text(original.replace(good, bad, 1), encoding="utf-8")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "-x", "-k", selector],
            capture_output=True, text=True, timeout=900,
        )
    finally:
        path.write_text(original, encoding="utf-8")     # 반드시 되돌린다
    return "잡음" if done.returncode else "놓침"


def main(argv):
    keep = argv[1] if len(argv) > 1 else ""
    cases = [c for c in CASES if keep in c[0] or keep in c[1]]
    if not cases:
        print("고른 것이 없다: %r" % keep)
        return 2

    bad = []
    for label, filename, good, seed, selector in cases:
        verdict = run_case(label, filename, good, seed, selector)
        print("%-9s %-26s %-14s %s" % (verdict, label, filename, selector))
        if verdict != "잡음":
            bad.append(label)

    print("\n%d/%d 잡음" % (len(cases) - len(bad), len(cases)))
    if bad:
        print("확인이 안 된 것: %s" % ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
