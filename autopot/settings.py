"""圖形介面版的設定檔（settings.yaml，放在 exe 旁邊）。

跟命令列版的 config.yaml 分開，欄位一樣（補貨程式 Runner 直接吃這份 dict）。
沒有設定檔時用 DEFAULTS；舊的設定檔少了新版才有的欄位，也會用 DEFAULTS 補上。
"""
from __future__ import annotations

import copy

import yaml

from . import calib

FILE_NAME = "settings.yaml"

DEFAULTS: dict = {
    "window_title": "MapleStory Worlds",
    "window_exclude": [],
    "level": None,
    "check_interval_sec": 2,
    "ocr_enabled": True,
    "buy": {},                       # 掃描商店後才會有：{"hp": {...}, "mp": {...}}
    "max_per_purchase": 9999,
    "sell_enabled": True,
    "sell": [],
    "sell_threshold": 0.93,
    "safety": {
        "timer_refill_hours": 3,
        "hp_emergency": 0.35,
        "hp_emergency_checks": 3,
        "mp_emergency": 0.08,
        "mp_emergency_checks": 10,
        "dead_hp": 0.02,
        "dead_checks": 3,
        "min_refill_gap_sec": 120,
        "emergency_recheck_sec": 60,
        "max_refill_failures": 3,
        "defer_if_user_active_sec": 60,
        "max_use_per_sec": 3,
        "ocr_jump_buffer": 60,
    },
    "notify": {"beep": True, "discord_webhook": "", "status_every_min": 0},
    "match_threshold": 0.85,
    "close_inventory_after": True,
    "arrange_windows": False,
    "hotkeys": {"pause": "f9", "refill_now": "f10", "quit": "f12"},
}

# 一種補品的預設值（掃描商店挑好之後用）
BUY_DEFAULTS = {"hp": {"target": 3000, "refill_below": 500, "fallback_buy": 1500},
                "mp": {"target": 1000, "refill_below": 200, "fallback_buy": 500}}


def path():
    return calib.ROOT / FILE_NAME


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "buy":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load() -> dict:
    p = path()
    data = {}
    if p.exists():
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    return _merge(DEFAULTS, data)


def save(cfg: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "# 楓之谷自動補水（圖形介面版）的設定。用程式裡的「設定」頁修改就好，不用手動改這個檔案\n"
    text += yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    p.write_text(text, encoding="utf-8")


def problems(cfg: dict) -> list[str]:
    """還不能開始監控的原因（給介面顯示）。"""
    out = []
    cal = calib.Calibration.load()
    miss = cal.missing()
    if miss:
        out.append("還沒做自動校正（設定 → 第 1 步）")
    if not cfg.get("buy"):
        out.append("還沒選要買的補品（設定 → 第 2 步：掃描妙妙商店）")
    for k, b in (cfg.get("buy") or {}).items():
        if not cal.has_template(b.get("template", "")):
            out.append(f"補品「{b.get('name', k)}」的圖片不見了，請重新掃描商店")
        q = b.get("quickslot")
        if q and not cal.has_region(q):
            out.append(f"補品「{b.get('name', k)}」的快捷欄位置還沒校正，請重新做自動校正")
    return out
