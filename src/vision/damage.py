"""傷害數字偵測——「有沒有真的打到東西」的直接證據。

為什麼不是偵測怪物：實機量測（27 幀連續畫面）顯示模板比對在這張地圖上
只找得到約 8 隻鱷魚中的 2 隻，加上鏡像模板變成 4 隻，代價是 1274 ms/幀。
對一個 0.3 秒的迴圈完全不可用，而且漏掉的那一半正好造成「明明有怪卻不打」
或「沒有怪卻照打」。

傷害數字反過來直接回答問題：打中了就會跳數字，沒打中就不會。實測整張
正規化畫面 35 ms/幀，而且不需要任何模板——換地圖、換怪都能用。

分辨傷害數字與 UI 粉紅（血條、聊天、右側通知）的關鍵是**時間穩定性**：
傷害數字會浮起後消失，UI 是靜止的。在最近幾幀大多時間都是桃紅的像素判定
為 UI，扣掉之後剩下的就是傷害數字。
"""

try:
    import cv2
    import numpy as np
    _CV_AVAILABLE = True
except Exception:                                    # pragma: no cover
    cv2 = None
    np = None
    _CV_AVAILABLE = False


class DamageWatcher:
    """看畫面上有沒有跳傷害數字。

    參數（皆由實機 27 幀量測定出）：
        h_lo/h_hi   桃紅色相帶（環繞 0）。實測傷害數字 H 中位數 166、S 218、V 237。
        s_min/v_min 高飽和且亮——遊戲背景的粉紅花草達不到。
        decay       靜態遮罩的學習速度（幀）。越大越慢適應，但越不會把
                    停留較久的數字誤認成 UI。
        static_frac 最近這麼高比例的時間都是桃紅 → 判定為 UI。
        min_area    夠大的色塊才算數字（濾掉零星像素）。基準尺度像素。
        warmup      靜態遮罩學好之前不回報——否則第一幀整片 UI 都會被當成傷害。
    """

    def __init__(self, h_lo=158, h_hi=6, s_min=150, v_min=180,
                 decay=8, static_frac=0.6, min_area=130, warmup=6,
                 side_margin=0.10, top_frac=0.06, bottom_frac=0.88):
        self.h_lo, self.h_hi = h_lo, h_hi
        self.s_min, self.v_min = s_min, v_min
        self.decay = max(1, int(decay))
        self.static_frac = float(static_frac)
        self.min_area = int(min_area)
        self.warmup = int(warmup)
        # 只看遊戲畫面區：上方任務列、下方 UI 列、左右邊緣的通知文字都是粉紅重災區
        self.side_margin = float(side_margin)
        self.top_frac = float(top_frac)
        self.bottom_frac = float(bottom_frac)
        self._acc = None
        self._seen = 0

    @property
    def ready(self):
        """靜態遮罩學好了沒。沒學好前一律回報「沒有傷害」而不是亂報。"""
        return self._seen >= self.warmup

    def _pink(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        return (((h >= self.h_lo) | (h <= self.h_hi))
                & (s > self.s_min) & (v > self.v_min)).astype(np.uint8)

    def update(self, frame):
        """吃一幀，回傳這一幀的傷害數字色塊 [(cx, cy, w, h, area), ...]。

        必須每幀都呼叫（含沒在攻擊的時候），靜態遮罩才學得準。
        """
        if not _CV_AVAILABLE or frame is None:
            return []
        m = self._pink(frame)
        if self._acc is None:
            self._acc = m.astype(np.float32)
        static = self._acc >= self.static_frac
        hits = []
        if self.ready:
            trans = ((m == 1) & ~static).astype(np.uint8)
            fh, fw = trans.shape[:2]
            x0, x1 = int(fw * self.side_margin), int(fw * (1 - self.side_margin))
            y0, y1 = int(fh * self.top_frac), int(fh * self.bottom_frac)
            mask = np.zeros_like(trans)
            mask[y0:y1, x0:x1] = 1
            trans = trans * mask
            # 數字是橫向連在一起的字元 → 用扁的核把同一組數字接成一塊
            trans = cv2.morphologyEx(trans, cv2.MORPH_CLOSE,
                                     np.ones((3, 9), np.uint8))
            n, _lab, st, _cen = cv2.connectedComponentsWithStats(trans, 8)
            for k in range(1, n):
                x, y, w, h, a = st[k]
                if a >= self.min_area:
                    hits.append((int(x + w // 2), int(y + h // 2),
                                 int(w), int(h), int(a)))
        self._acc = self._acc * (1 - 1.0 / self.decay) + m * (1.0 / self.decay)
        self._seen += 1
        return hits

    def reset(self):
        """換地圖／視窗大小變了 → 重新學靜態遮罩。"""
        self._acc = None
        self._seen = 0
