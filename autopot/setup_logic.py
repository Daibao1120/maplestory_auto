"""設定頁的邏輯（跟畫面分開，才能寫單元測試）：掃描結果 → 存模板、寫進設定。"""
from __future__ import annotations

import copy

from . import catalog
from .settings import BUY_DEFAULTS

KIND_LABEL = {"hp": "HP 補品", "mp": "MP 補品"}


def apply_buy(cfg: dict, kind: str, item: catalog.ListItem, items: list[catalog.ListItem], name: str,
              quickslot: str | None, target: int | None = None, refill_below: int | None = None,
              fallback_buy: int | None = None) -> dict:
    """把掃描到的商品 item 設成 kind（hp / mp）要買的補品。回傳更新後的設定（也會改 cfg 本身）。"""
    tpl = f"buy_{kind}"
    catalog.save_item_template(tpl, item)
    th, worst = catalog.threshold_for(item, items)
    old = (cfg.get("buy") or {}).get(kind, {})
    d = BUY_DEFAULTS[kind]
    b = {
        "name": name or old.get("name") or KIND_LABEL[kind],
        "template": tpl,
        "quickslot": quickslot or None,
        "target": int(target if target is not None else old.get("target", d["target"])),
        "refill_below": int(refill_below if refill_below is not None else old.get("refill_below", d["refill_below"])),
        "fallback_buy": int(fallback_buy if fallback_buy is not None else old.get("fallback_buy", d["fallback_buy"])),
        "threshold": th,
        "learned": True,
        "similar": worst,
    }
    cfg.setdefault("buy", {})[kind] = b
    _sanity(cfg)
    return cfg


def remove_buy(cfg: dict, kind: str) -> dict:
    (cfg.get("buy") or {}).pop(kind, None)
    _sanity(cfg)
    return cfg


def apply_sell(cfg: dict, picks: list[tuple[catalog.ListItem, str, int]], items: list[catalog.ListItem]) -> dict:
    """picks = [(道具, 名稱, 保留幾組), ...]：設成要賣的東西（會取代原本的清單）。"""
    sell = []
    for i, (it, name, keep) in enumerate(picks, 1):
        tpl = f"sell_item{i}"
        catalog.save_item_template(tpl, it)
        th, worst = catalog.threshold_for(it, items, base=0.93)
        sell.append({"template": tpl, "name": name or f"道具{i}", "keep_stacks": max(0, int(keep)),
                     "threshold": th, "learned": True})
    cfg["sell"] = sell
    cfg["sell_enabled"] = bool(sell)
    return cfg


def _sanity(cfg: dict) -> None:
    """沒有 MP 補品就不要做「MP 太低 → 緊急補貨」（買不到 MP 水，只會一直開商店）。"""
    buy = cfg.get("buy") or {}
    sf = cfg.setdefault("safety", {})
    if "mp" not in buy:
        sf["mp_emergency"] = 0
    elif not sf.get("mp_emergency"):
        sf["mp_emergency"] = 0.08


def warnings_for(cfg: dict) -> list[str]:
    out = []
    for k, b in (cfg.get("buy") or {}).items():
        if (b.get("similar") or 0) >= 0.95:
            out.append(f"「{b['name']}」跟商店裡別的商品長得幾乎一樣，買之前程式會再逐點確認，確認不了就不買")
        if not b.get("quickslot"):
            out.append(f"「{b['name']}」沒有放快捷欄 → 讀不到剩幾個，只會每 {cfg['safety']['timer_refill_hours']} 小時定時補")
    return out


def summary(cfg: dict) -> str:
    buy = cfg.get("buy") or {}
    if not buy:
        return "還沒選補品"
    parts = []
    for k in ("hp", "mp"):
        if k in buy:
            b = buy[k]
            qs = b.get("quickslot")
            parts.append(f"{KIND_LABEL[k]}：{b['name']}（快捷欄 {qs[3:].upper() if qs else '沒放'}）")
    if cfg.get("sell"):
        parts.append("要賣：" + "、".join(f"{s['name']}" + (f"（留 {s['keep_stacks']} 組）" if s["keep_stacks"] else "")
                                         for s in cfg["sell"]))
    return "\n".join(parts)


def copy_cfg(cfg: dict) -> dict:
    return copy.deepcopy(cfg)
