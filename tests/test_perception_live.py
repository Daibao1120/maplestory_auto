"""對著實機畫面驗證感知層——這個檔案存在的理由。

以下四個缺陷同時存在時，其餘 273 項測試**全部保持綠燈**。它們都是離線合成
資料看不出來、對著真畫面跑一次就現形的東西：

  1. HP 條讀到的是底部 UI 的購物商場紅色按鈕。血量 24% 時真血條長 49px，
     那顆按鈕也剛好 49px，掃描順序讓按鈕先贏 → HP 讀值恆為 1.000 →
     血量保護與 HP_LOST_LIMIT 從此不可能觸發，而且每幀都回報「找到血條」。
  2. EXP 區因此被推算到隊伍視窗的成員血條上 → 進帳偵測亂跳 → 停滯 watchdog
     的時鐘被一直重設，永遠不會觸發。
  3. 小地圖自動框回傳 (0,0,820,558)，整個左上象限含使用者開著的視窗 →
     玩家點鎖在視窗裡的靜態圖示，座標完全不動 → 永遠不重新校準 →
     平台量測恆為 None → 45 秒後進 IDLE_SAFE 卡到天亮。
  4. 平台寬度取「第一列」而非「最寬列」，量到 6px（地面實際 180px）→
     PATROL_MIN_WIDTH=12 永遠過不了，巡邏從來沒運作過。

fixture 是 2026-08-26 使用者實際遊玩時擷取的（戰火之地：沼澤地III，獵人 lv44，
畫面上有開著的 UI 視窗與其他玩家）。只保留底部狀態列與左上小地圖象限、其餘
塗黑後存成 PNG——UI 判讀對顏色極敏感（中性灰空槽 vs 按鈕邊框相差只有十幾階），
JPEG 會把判別破壞掉，而塗黑之後 PNG 無損且極小。
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

cv2 = pytest.importorskip("cv2")
yaml = pytest.importorskip("yaml")

from src.vision import (MinimapLocator, PlayerTracker, exp_text_roi_from_bars,  # noqa: E402
                        find_bars_pair, region_changed)
from src.vision.ui_calibrate import find_minimap_rect  # noqa: E402

LIVE = os.path.join(ROOT, "tests", "fixtures", "live")
CANON = sorted(glob.glob(os.path.join(LIVE, "[0-9][0-9].png")))
RAW = os.path.join(LIVE, "raw_000.png")


@pytest.fixture(scope="module")
def frames():
    if not CANON:
        pytest.skip("沒有實機 fixture")
    return [cv2.imread(f) for f in CANON]


@pytest.fixture(scope="module")
def cfg():
    with open(os.path.join(ROOT, "config", "settings.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---- 1. HP 條 ----

def test_hp_bar_is_the_real_bar_not_the_shop_button(frames):
    """真血條右邊接著中性灰的空槽；按鈕右邊是圖示邊框。

    兩者在血量 24% 時長度都是 49px，只靠「取最長」必定選錯。
    """
    xs = set()
    for f in frames:
        hp, _mp = find_bars_pair(f)
        assert hp, "一幀都找不到血條"
        xs.add(hp["x"])
    assert len(xs) == 1, f"血條位置在幀之間跳動：{sorted(xs)}"
    x = xs.pop()
    assert x < 700, f"血條被判在 x={x}（畫面右半＝底部 UI 按鈕區）"


def test_hp_length_varies_because_it_tracks_real_health(frames):
    """長度完全不變＝抓到靜態 UI。真血條會隨血量變動。"""
    lens = [find_bars_pair(f)[0]["len"] for f in frames]
    assert len(set(lens)) > 1, f"血條長度 164 幀完全不變（{lens[0]}）→ 抓到靜態 UI"


# ---- 2. EXP 區 ----

def test_exp_roi_is_stable_and_not_the_party_window(frames):
    rois = []
    for f in frames:
        hp, mp = find_bars_pair(f)
        r = exp_text_roi_from_bars(f, hp, mp)
        assert r, "推算不出 EXP 區"
        rois.append(tuple(int(v) for v in r))
    xs = [r[0] for r in rois]
    assert max(xs) - min(xs) <= 20, f"EXP 區在幀之間漂移：{sorted(set(xs))}"
    assert max(xs) < 1000, f"EXP 區被推到 x={max(xs)}（隊伍視窗那一側）"


def test_exp_change_detection_is_not_permanently_on(frames):
    """每一幀都判定『有進帳』→ 停滯 watchdog 的時鐘被一直重設，永遠不觸發。"""
    prev = None
    hits = 0
    for f in frames:
        hp, mp = find_bars_pair(f)
        x, y, w, h = (int(v) for v in exp_text_roi_from_bars(f, hp, mp))
        changed, prev = region_changed(prev, f[y:y + h, x:x + w].astype("int16"))
        hits += bool(changed)
    assert hits < len(frames), "每一幀都說有進帳 → 停滯偵測形同虛設"


# ---- 3. 小地圖自動框 ----

def test_minimap_auto_rect_does_not_swallow_the_ui_windows():
    """框到整個左上象限會讓玩家點鎖在視窗圖示上，然後整夜卡住。"""
    if not os.path.exists(RAW):
        pytest.skip("沒有原始尺度 fixture")
    raw = cv2.imread(RAW)
    rect = find_minimap_rect(raw)
    if rect is None:
        return                       # 找不到就沿用設定檔 ROI，是安全的結果
    x, y, w, h = rect
    fh, fw = raw.shape[:2]
    assert w * h <= fh * fw * 0.05, f"自動框 {rect} 佔畫面 {w*h/(fh*fw):.1%}，太大"


# ---- 4. 平台寬度 ----

def test_platform_span_finds_the_ground_not_the_grass_fringe(frames, cfg):
    """玩家腳下有一條稀疏草緣只有 6px 寬，會先被選中；真地面在下一列、180px。

    量到 6 的後果是 PATROL_MIN_WIDTH=12 永遠過不了，巡邏從來沒運作過。
    """
    mm = MinimapLocator(cfg["vision"]["minimap"])
    tr = PlayerTracker()
    widths = []
    for f in frames:
        pos = tr.update(mm.locate_player_candidates(f))
        span = mm.platform_span(f, pos) if pos else None
        if span:
            widths.append(span["width"])
    assert widths, "一幀都量不到平台"
    med = sorted(widths)[len(widths) // 2]
    assert med > 100, f"平台寬度中位數只有 {med}（實際地面約 180）"


def test_measured_width_clears_the_patrol_threshold(frames, cfg):
    from tools.overnight import NightWatchCore
    mm = MinimapLocator(cfg["vision"]["minimap"])
    tr = PlayerTracker()
    ok = 0
    n = 0
    for f in frames:
        pos = tr.update(mm.locate_player_candidates(f))
        span = mm.platform_span(f, pos) if pos else None
        if span:
            n += 1
            ok += span["width"] >= NightWatchCore.PATROL_MIN_WIDTH
    assert n and ok / n > 0.9, f"只有 {ok}/{n} 幀過得了巡邏門檻"
