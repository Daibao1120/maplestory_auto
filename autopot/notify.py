"""通知：嗶聲、Discord Webhook、記錄檔。"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import urllib.request

log = logging.getLogger("autopot")


class Notifier:
    def __init__(self, cfg: dict):
        self.beep = cfg.get("beep", True)
        self.webhook = (cfg.get("discord_webhook") or "").strip()
        self._last: dict[str, float] = {}

    def _beep(self, times: int) -> None:
        if not self.beep or sys.platform != "win32":
            return
        import winsound

        def run():
            for _ in range(times):
                winsound.Beep(1800, 250)
                time.sleep(0.15)
        threading.Thread(target=run, daemon=True).start()

    def _discord(self, text: str) -> None:
        if not self.webhook:
            return

        def run():
            try:
                req = urllib.request.Request(
                    self.webhook, data=json.dumps({"content": text[:1900]}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "User-Agent": "maple-autopot"})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # 通知失敗不影響主程式
                log.warning("Discord 通知失敗：%s", e)
        threading.Thread(target=run, daemon=True).start()

    def send(self, level: str, msg: str, cooldown: float = 0) -> None:
        """level: info / warn / critical。cooldown 秒內同樣訊息不重複發。"""
        now = time.time()
        if cooldown and now - self._last.get(msg, 0) < cooldown:
            return
        self._last[msg] = now
        icon = {"info": "ℹ️", "warn": "⚠️", "critical": "🚨"}.get(level, "")
        getattr(log, {"info": "info", "warn": "warning", "critical": "critical"}[level])(msg)
        if level == "critical":
            self._beep(6)
        elif level == "warn":
            self._beep(2)
        if level != "info" or msg.startswith("[狀態]"):
            self._discord(f"{icon} 楓之谷自動補水：{msg}")
