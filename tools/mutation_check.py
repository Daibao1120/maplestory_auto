# -*- coding: utf-8 -*-
"""Mutation 測試：故意破壞每條安全規則，測試套件必須抓到（否則測試是空的）。"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = r"E:\maplestory_auto-main"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SRC = os.path.join(ROOT, "tools", "overnight.py")

MUTATIONS = [
    ("移除環境異常時的放開攻擊鍵",
     "        if not w.window or not w.frame_ok or not w.fg:\n            self._release_attack(acts)\n            return acts",
     "        if not w.window or not w.frame_ok or not w.fg:\n            return acts"),
    ("移除前景檢查",
     "if not w.window or not w.frame_ok or not w.fg:",
     "if not w.window or not w.frame_ok:"),
    ("低血保險門檻改 0（立刻撤 → 應被『恰 0.45 不撤』測試抓到）",
     "    HP_ABORT = 0.45", "    HP_ABORT = 0.50"),
    ("低血持續時間改 0",
     "    HP_ABORT_HOLD = 12.0", "    HP_ABORT_HOLD = 0.0"),
    ("移除 HP 讀不到看門狗",
     '                ("_hp_lost_since", w.hp is not None, self.HP_LOST_LIMIT, "HP 讀不到過久")):',
     '                ):'),
    ("移除平台讀不到看門狗",
     '                ("_span_lost_since", w.span is not None, self.SPAN_LOST_LIMIT, "平台讀不到過久"),',
     ''),
    ("位置/平台消失時不放開攻擊鍵",
     "        if w.pos is None or w.span is None:\n            self._release_attack(acts)",
     "        if w.pos is None or w.span is None:\n            pass"),
    ("EXP 停滯改成進 IDLE_SAFE（而非零輸入靜默）",
     '            self._to("IDLE_SILENT", acts, "EXP 停滯過久（測謊視窗/異常？）→ 零輸入", w)',
     '            self._to("IDLE_SAFE", acts, "EXP 停滯過久", w)'),
    ("IDLE_SILENT 不再靜默（允許動作）",
     '        if self.state == "IDLE_SILENT":\n            self._release_attack(acts)',
     '        if False:\n            self._release_attack(acts)'),
    ("FARM 重入不重置 EXP 時戳",
     '            self._last_exp_ts = None          # 重入不吃舊時間戳（防假性停滯）',
     '            pass'),
    ("下降預算不重置",
     "            self._descend_count = 0           # 每回合獨立步數預算",
     "            pass"),
    ("驗證失敗一次就永久判死",
     "    POTION_FAILS_TO_CONDEMN = 2", "    POTION_FAILS_TO_CONDEMN = 1"),
    ("移除絕望補血",
     "        if (w.hp < self.HP_DESPERATE\n                and (self._last_desperate is None or w.now - self._last_desperate > 5.0)):",
     "        if False:"),
    ("讓手不再放開全部按鍵",
     '                acts.append(Action("release_all"))\n                self.stats.yields += 1',
     '                self.stats.yields += 1'),
    ("掉到窄平台仍繼續打",
     "            if w.span[\"width\"] < self.FARMABLE_MIN:",
     "            if False:"),
    ("IDLE_SAFE 掉血不升級靜默",
     "            if w.hp < self.HP_ABORT or drop > self.IDLE_HP_DROP_SILENT:",
     "            if False:"),
]

orig = open(SRC, encoding="utf-8").read()
backup = tempfile.mktemp(suffix=".py")
shutil.copy(SRC, backup)
caught = killed = 0
try:
    for name, old, new in MUTATIONS:
        if old not in orig:
            print(f"[跳過] 找不到片段：{name}")
            continue
        open(SRC, "w", encoding="utf-8").write(orig.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "pytest", "-q", os.path.join(ROOT, "tests", "test_overnight.py")],
                           capture_output=True, text=True, cwd=ROOT,
                           encoding="utf-8", errors="replace")
        ok = r.returncode != 0
        caught += 1
        killed += ok
        tail = [l for l in r.stdout.strip().splitlines() if "passed" in l or "failed" in l]
        print(f"[{'✔ 抓到' if ok else '✘ 漏掉'}] {name} — {tail[-1] if tail else ''}")
finally:
    shutil.copy(backup, SRC)
    os.remove(backup)
print(f"\n結果：{killed}/{caught} 個蓄意破壞被測試抓到")
r = subprocess.run([PY, "-m", "pytest", "-q", os.path.join(ROOT, "tests")],
                   capture_output=True, text=True, cwd=ROOT,
                           encoding="utf-8", errors="replace")
print("還原後全套：", r.stdout.strip().splitlines()[-1])
