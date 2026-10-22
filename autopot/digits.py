"""快捷欄數字辨識（楓之谷像素字型）。

做法：每個數字 0-9 有一張「彩色模板 + 遮罩」，遮罩只涵蓋字形本身
（黑色外框與白/淺藍填色），背景的道具圖示不列入比對，所以不受圖示影響。
字型是比例字（1 比較窄），用每個數字自己的字寬往右推進。

模板可以從使用者自己的畫面「學習」(learn)，用來適應不同的視窗大小。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

DIGITS = "0123456789"


def stroke_map(img: np.ndarray) -> np.ndarray:
    """把畫面轉成「數字筆畫圖」：只留下被黑框夾住的細白/淺藍筆畫，其他（道具圖示）都變黑。

    楓之谷的數字是白色→淺藍漸層填色（B=255），外面一圈純黑框，
    道具圖示的大片白色不會被黑框緊緊夾住，所以會被去掉。"""
    b, g, r = [img[:, :, i].astype(np.int16) for i in range(3)]
    fill = (b >= 240) & (g >= 180) & (r >= 120) & (b >= g) & (g >= r - 5)
    dark = img.max(axis=2) <= 30

    def near(mask, dx, dy, k=5):
        acc = np.zeros_like(mask)
        for st in range(1, k + 1):
            acc |= np.roll(mask, (dy * st, dx * st), axis=(0, 1))
        return acc

    thin = (near(dark, 1, 0) & near(dark, -1, 0)) | (near(dark, 0, 1) & near(dark, 0, -1))
    m = (fill & thin).astype(np.float32) * 255
    return np.repeat(m[:, :, None], 3, axis=2)


@dataclass
class DigitTemplates:
    tpl: dict[str, np.ndarray]          # d -> HxWx3 float32
    mask: dict[str, np.ndarray]         # d -> HxW bool
    adv: dict[str, int]                 # d -> 字寬（往右推進的像素）
    meta: dict = field(default_factory=dict)

    @property
    def height(self) -> int:
        return next(iter(self.tpl.values())).shape[0]

    @property
    def width(self) -> int:
        return next(iter(self.tpl.values())).shape[1]

    def save(self, path: str | Path) -> None:
        arrays = {}
        for d in DIGITS:
            arrays["t" + d] = self.tpl[d].astype(np.float32)
            arrays["m" + d] = self.mask[d]
        arrays["adv"] = np.array(json.dumps(self.adv))
        arrays["meta"] = np.array(json.dumps(self.meta))
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path: str | Path) -> "DigitTemplates":
        z = np.load(path, allow_pickle=False)
        adv = {k: int(v) for k, v in json.loads(str(z["adv"])).items()}
        meta = json.loads(str(z["meta"])) if "meta" in z else {}
        return cls(
            tpl={d: z["t" + d].astype(np.float32) for d in DIGITS},
            mask={d: z["m" + d].astype(bool) for d in DIGITS},
            adv=adv,
            meta=meta,
        )


class DigitReader:
    def __init__(self, templates: DigitTemplates, max_dist: float = 22.0):
        self.T = templates
        self.max_dist = max_dist

    def _prep(self, img: np.ndarray) -> np.ndarray:
        if self.T.meta.get("pre") == "stroke":
            return np.ascontiguousarray(stroke_map(img))
        return np.ascontiguousarray(img, dtype=np.float32)

    # 對整張區域算出每個位置的距離圖（越小越像）。用 OpenCV 加遮罩的 SQDIFF，速度快。
    def _maps(self, reg: np.ndarray) -> dict[str, np.ndarray]:
        if self.T.meta.get("pre") == "stroke":
            return self._maps_stroke(reg)
        out = {}
        for d in DIGITS:
            t, m = self.T.tpl[d], self.T.mask[d]
            if reg.shape[0] < t.shape[0] or reg.shape[1] < t.shape[1]:
                return {}
            m3 = np.repeat(m[:, :, None], 3, axis=2).astype(np.float32)
            r = cv2.matchTemplate(reg, t, cv2.TM_SQDIFF, mask=m3)
            out[d] = np.sqrt(np.maximum(r, 0) / (max(1, m.sum()) * 3))
        return out

    def _maps_stroke(self, reg: np.ndarray) -> dict[str, np.ndarray]:
        """筆畫圖用 Dice 相似度：(1 - 2|A∩B| / (|A|+|B|)) x 100，只算模板框內。"""
        b = (reg[:, :, 0] > 127).astype(np.float32)
        out = {}
        for d in DIGITS:
            t = (self.T.tpl[d][:, :, 0] > 127).astype(np.float32)
            box = self.T.mask[d].astype(np.float32)
            t = t * box
            if b.shape[0] < t.shape[0] or b.shape[1] < t.shape[1]:
                return {}
            inter = cv2.matchTemplate(b, t, cv2.TM_CCORR)
            nb = cv2.matchTemplate(b, box, cv2.TM_CCORR)
            na = float(t.sum())
            out[d] = (1 - 2 * inter / np.maximum(na + nb, 1)) * 100
        return out

    def read_detail(self, region_bgr: np.ndarray, start_x: int | None = None, start_tol: int = 4):
        """回傳 (數字字串或 None, 每位數距離, 每位數 (x,y))

        start_x：數字第一位應該出現的 x 位置（數字一定靠左對齊，位置固定）。
        有給的話，第一位不在這附近就當作讀不到，避免漏掉第一位而讀成較小的數字。"""
        reg = self._prep(region_bgr)
        maps = self._maps(reg)
        if not maps:
            return None, [], []
        stack = np.stack([maps[d] for d in DIGITS])          # 10 x H x W
        best_d = stack.argmin(0)
        best_s = stack.min(0)
        col = best_s.min(0)                                   # 每個 x 的最佳分數
        ok = np.where(col <= self.max_dist)[0]
        if len(ok) == 0:
            return None, [], []
        # 第一位數 = 最左邊、且在附近（4px 內）分數最低的那個位置
        if start_x is not None:
            lo, hi = max(0, start_x - start_tol), min(len(col), start_x + start_tol + 1)
            if lo >= hi or col[lo:hi].min() > self.max_dist:
                return None, [], []
            x = lo + int(np.argmin(col[lo:hi]))
        else:
            x0 = int(ok[0])
            win = range(x0, min(x0 + 5, len(col)))
            x = min(win, key=lambda i: col[i])
        y = int(best_s[:, x].argmin())
        d = DIGITS[best_d[y, x]]
        out, scores, pos = d, [float(best_s[y, x])], [(x, y)]
        Hm, Wm = best_s.shape
        for _ in range(7):
            gx = x + self.T.adv[d]
            cand = None
            for dx in range(-3, 4):
                for dy in (-1, 0, 1):
                    xx, yy = gx + dx, y + dy
                    if 0 <= xx < Wm and 0 <= yy < Hm:
                        s = float(best_s[yy, xx])
                        if cand is None or s < cand[0]:
                            cand = (s, xx, yy)
            if cand is None or cand[0] > self.max_dist:
                break
            _, x, y = cand
            d = DIGITS[best_d[y, x]]
            out += d
            scores.append(cand[0])
            pos.append((x, y))
        return out, scores, pos

    def read(self, region_bgr: np.ndarray, start_x: int | None = None) -> int | None:
        txt, _, _ = self.read_detail(region_bgr, start_x)
        if not txt:
            return None
        if len(txt) > 1 and txt[0] == "0":  # 開頭為 0 的多位數不合理
            return None
        return int(txt)


def _trim_to_glyph(mk: np.ndarray, g: np.ndarray, sd: np.ndarray, pad: int = 3) -> np.ndarray:
    """只保留「這個數字本身」的欄位，切掉左右兩邊鄰近數字的殘影。

    以穩定的亮色填色所在欄位為準，取最靠近中央的那一段，再往左右各多留 pad 欄給黑色外框。"""
    fill_cols = ((g > 150) & (sd < 18)).sum(axis=0) >= 2
    if not fill_cols.any():
        return mk
    w = len(fill_cols)
    segs, start, gap = [], None, 0
    for x in range(w + 1):
        on = x < w and fill_cols[x]
        if on:
            if start is None:
                start = x
            gap = 0
            end = x
        elif start is not None:
            gap += 1
            if gap > 2 or x == w:
                segs.append((start, end))
                start, gap = None, 0
    if not segs:
        return mk
    c = w / 2
    s0, s1 = min(segs, key=lambda se: 0 if se[0] <= c <= se[1] else min(abs(se[0] - c), abs(se[1] - c)))
    out = mk.copy()
    out[:, : max(0, s0 - pad)] = False
    out[:, s1 + pad + 1:] = False
    return out


def scaled(base: DigitTemplates, k: float) -> DigitTemplates:
    """把模板整體縮放 k 倍（給不同視窗大小當起點用）。"""
    if abs(k - 1) < 0.01:
        return base
    h, w = base.height, base.width
    H, W = max(8, round(h * k)), max(6, round(w * k))
    tpl = {d: cv2.resize(base.tpl[d], (W, H), interpolation=cv2.INTER_LINEAR) for d in DIGITS}
    mask = {d: cv2.resize(base.mask[d].astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
            for d in DIGITS}
    adv = {d: max(1, round(base.adv[d] * k)) for d in DIGITS}
    return DigitTemplates(tpl, mask, adv, dict(base.meta, scale=base.meta.get("scale", 1.0) * k))


def estimate_scale(samples: list[tuple[np.ndarray, str]], base: DigitTemplates) -> float:
    """試幾個縮放倍率，找讓已知答案最吻合的那個。"""
    r1 = DigitReader(base)
    if all(r1.read_detail(img)[0] == label for img, label in samples):
        return 1.0   # 預設模板就全部讀對 → 不用縮放
    best = None
    for k in np.arange(0.80, 1.31, 0.02):
        cand = scaled(base, float(k))
        r = DigitReader(cand, max_dist=1e9)
        hits, dist = 0, 0.0
        for img, label in samples:
            txt, sc, _ = r.read_detail(img)
            # 只看應有的位數，答對的字元越多越好
            if txt:
                hits += sum(a == b for a, b in zip(txt, label)) - abs(len(txt) - len(label))
                dist += float(np.mean(sc[: len(label)]))
        key = (hits, -dist - abs(k - 1) * 5)
        if best is None or key > best[0]:
            best = (key, float(k))
    return best[1]


def learn(samples: list[tuple[np.ndarray, str]], base: DigitTemplates,
          search_pad: int = 3, scale: float | None = None,
          pre: str | None = None) -> tuple[DigitTemplates, dict]:
    """用「已知答案」的截圖重新產生模板。

    samples: [(快捷欄區域 BGR 圖, "4360"), ...]
    以 base 模板對齊每個字的位置，再把同一數字的所有出現取平均，
    低變異且為黑/亮色的像素當作字形遮罩。沒出現過的數字沿用 base。
    """
    if scale is None:
        scale = estimate_scale(samples, base)
    orig = base
    base = scaled(base, scale)
    reader = DigitReader(base, max_dist=1e9)
    h, w = base.height, base.width
    occ: dict[str, list[np.ndarray]] = {d: [] for d in DIGITS}
    adv_obs: dict[str, list[int]] = {d: [] for d in DIGITS}
    occ_raw: dict[str, list[np.ndarray]] = {d: [] for d in DIGITS}
    out_pre = pre if pre is not None else base.meta.get("pre")
    for img, label in samples:
        reg = reader._prep(img)
        maps = reader._maps(reg)
        ext = np.ascontiguousarray(stroke_map(img)) if out_pre in ("stroke", "glyph") else np.ascontiguousarray(img, dtype=np.float32)
        raw = np.ascontiguousarray(img, dtype=np.float32)
        if not maps:
            continue
        Hm, Wm = maps[label[0]].shape
        # 嘗試多個起點，取整串總距離最小的對齊方式
        m0 = maps[label[0]][:, : max(1, Wm // 2)]
        starts = np.dstack(np.unravel_index(np.argsort(m0, axis=None)[:40], m0.shape))[0]
        best_path = None
        for (y0, x0) in starts:
            x, y, tot, path = int(x0), int(y0), float(m0[y0, x0]), [(int(x0), int(y0))]
            for k in range(1, len(label)):
                gx = x + base.adv[label[k - 1]]
                cand = None
                for dx in range(-search_pad, search_pad + 1):
                    for dy in (-1, 0, 1):
                        xx, yy = gx + dx, y + dy
                        if 0 <= xx < Wm and 0 <= yy < Hm:
                            v = float(maps[label[k]][yy, xx])
                            if cand is None or v < cand[0]:
                                cand = (v, xx, yy)
                if cand is None:
                    tot = 1e18
                    break
                tot += cand[0]
                x, y = cand[1], cand[2]
                path.append((x, y))
            if best_path is None or tot < best_path[0]:
                best_path = (tot, path)
        if best_path is None or len(best_path[1]) != len(label):
            continue
        path = best_path[1]
        for k, ch in enumerate(label):
            x, y = path[k]
            if k + 1 < len(path):
                adv_obs[ch].append(path[k + 1][0] - x)
            occ[ch].append(ext[y:y + h, x:x + w])
            occ_raw[ch].append(raw[y:y + h, x:x + w])
    tpl, mask, adv = {}, {}, {}
    report = {}
    for d in DIGITS:
        L = occ[d]
        adv[d] = int(round(np.median(adv_obs[d]))) if adv_obs[d] else base.adv[d]
        if out_pre == "glyph" and len(L) >= 1:
            A = np.stack(L)[:, :, :, 0]
            st = (A.mean(0) > 100).astype(np.uint8)
            colmask = _trim_to_glyph(np.ones(st.shape, dtype=bool), st.astype(np.float32) * 255,
                                     np.zeros(st.shape, np.float32), pad=4)
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            gm = (cv2.dilate(st, k) > 0) & colmask
            tpl[d] = np.stack(occ_raw[d]).mean(0).astype(np.float32)
            mask[d] = gm
            report[d] = f"學習 {len(L)} 次"
            continue
        if out_pre == "stroke" and len(L) >= 1:
            A = np.stack(L)[:, :, :, 0]
            m = (A.mean(0) > 127).astype(np.float32) * 255
            colmask = _trim_to_glyph(np.ones_like(m, dtype=bool), m, np.zeros_like(m), pad=4)
            tpl[d] = np.repeat(m[:, :, None], 3, axis=2)
            mask[d] = colmask
            report[d] = f"學習 {len(L)} 次"
            continue
        if len(L) >= 3 or (len(L) >= 2 and abs(scale - 1) > 0.03):
            A = np.stack(L)
            m = A.mean(0)
            sd = A.std(0).mean(axis=2)
            g = m.mean(axis=2)
            mk = (sd < 18) & ((g < 60) | (g > 150))
            mk = _trim_to_glyph(mk, g, sd)
            if mk.sum() > 80:
                tpl[d], mask[d] = m, mk
                report[d] = f"學習 {len(L)} 次"
                continue
        tpl[d], mask[d] = base.tpl[d], base.mask[d]
        report[d] = f"沿用預設（只出現 {len(L)} 次）"
    report["scale"] = round(scale, 2)
    if out_pre not in (base.meta.get("pre"),):
        # 換了前處理方式：沒學到的數字不能沿用舊模板
        missing = [d for d in DIGITS if not report[d].startswith("學習")]
        if missing:
            report["結果"] = f"數字 {''.join(missing)} 沒有樣本，無法建立新模板"
            return orig, report
    new = DigitTemplates(tpl, mask, adv, dict(base.meta, learned=True,
                                              pre=None if out_pre == "glyph" else out_pre))

    # 只有在「新模板讀對的比舊的多」時才採用，避免越學越差
    def score(T):
        r = DigitReader(T)
        return sum(r.read_detail(img)[0] == lab for img, lab in samples)
    old_s, new_s = score(orig), score(new)
    report["正確數（舊→新）"] = f"{old_s}→{new_s} / {len(samples)}"
    if new_s <= old_s and out_pre == orig.meta.get("pre") and out_pre != "glyph":
        report["結果"] = "新模板沒有比較好，保留原本的"
        return orig, report
    report["結果"] = "採用新模板"
    return new, report
