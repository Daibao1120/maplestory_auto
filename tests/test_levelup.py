"""升等時間預估（autopot/levelup.py）。

用真實截圖做的模擬遊戲（tests/expsim.py，家裡 2764x1555 的畫面）讓 EXP 條照固定速度漲，
檢查預估的時間跟「真正會升等的時間」差多少。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from autopot.levelup import EXP_TABLE, LevelForecast, fmt_dur  # noqa: E402
from expsim import ExpGame  # noqa: E402

PER_HOUR = 265_600_000          # 10/20 截圖：22.3 小時賺 59.3 億 ≈ 每小時 2.66 億


def run(game, f, seconds, step=2.0):
    end = game.clock.t + seconds
    while game.clock.t < end:
        f.update(game.capture(), game.clock.t)
        game.clock.sleep(step)


def test_table_matches_game():
    assert EXP_TABLE[196] == 1_713_628_833          # 跟遊戲裡顯示的一樣
    assert all(EXP_TABLE[lv + 1] > EXP_TABLE[lv] for lv in range(194, 199))


@pytest.mark.parametrize("base", ["home", "office"])
@pytest.mark.parametrize("start", [0.10, 0.50, 0.893])
def test_next_and_200_forecast_accuracy(tmp_path, base, start):
    """Lv196、速度每小時 2.66 億：觀察 10 分鐘後，預估要跟真實時間差不到 10%。"""
    need = EXP_TABLE[196]
    pct_per_min = PER_HOUR / need * 100 / 60
    g = ExpGame(base=base, ratio=start, pct_per_min=pct_per_min)
    f = LevelForecast(level=196, save_path=tmp_path / "level.json")
    run(g, f, 600)
    out = f.forecast(g.clock.t)
    true_next = (1 - g.ratio) * need / (PER_HOUR / 3600)
    true_200 = true_next + sum(EXP_TABLE[lv] for lv in (197, 198, 199)) / (PER_HOUR / 3600)
    assert out["next"] == pytest.approx(true_next, rel=0.10, abs=120)
    assert out["to200"] == pytest.approx(true_200, rel=0.10)
    assert out["per_hour"] == pytest.approx(PER_HOUR, rel=0.10)
    txt = f.text(g.clock.t)
    assert "下次升等" in txt and "到 200 等" in txt


def test_needs_some_minutes_first(tmp_path):
    g = ExpGame(base="home", ratio=0.5, pct_per_min=0.26)
    f = LevelForecast(level=196, save_path=tmp_path / "l.json")
    run(g, f, 120)
    assert f.forecast(g.clock.t)["next"] is None
    assert "觀察中" in f.text(g.clock.t)


def test_level_up_detected_saved_and_forecast_continues(tmp_path):
    """升等時（EXP 從快滿掉回 0）自動 +1、存檔，速度不會因為歸零被算成負的。"""
    need = EXP_TABLE[196]
    g = ExpGame(base="home", ratio=0.95, pct_per_min=PER_HOUR / need * 100 / 60)
    p = tmp_path / "level.json"
    f = LevelForecast.load(196, p)
    run(g, f, 30 * 60)                    # 中間會升到 197
    assert g.level == 1 and f.level == 197
    assert json.loads(p.read_text(encoding="utf-8"))["level"] == 197
    out = f.forecast(g.clock.t)
    # 模擬遊戲是「每分鐘漲固定 %」，所以升到 197 後每小時經驗跟著變多（197 級一條比較長）
    per_hour_197 = PER_HOUR * EXP_TABLE[197] / EXP_TABLE[196]
    assert out["per_hour"] == pytest.approx(per_hour_197, rel=0.10)
    true_next = (1 - g.ratio) * EXP_TABLE[197] / (per_hour_197 / 3600)
    assert out["next"] == pytest.approx(true_next, rel=0.10)
    # 重開程式：config 還是寫 196（你沒改）→ 用存下來的 197
    assert LevelForecast.load(196, p).level == 197
    # 你自己改了 config（例如程式沒開時升等了）→ 以你填的為準
    assert LevelForecast.load(198, p).level == 198


def test_not_hunting_says_so(tmp_path):
    g = ExpGame(base="home", ratio=0.4, pct_per_min=0.3, hunting=False)
    f = LevelForecast(level=196, save_path=tmp_path / "l.json")
    run(g, f, 600)
    assert f.forecast(g.clock.t)["next"] is None
    assert "沒有在漲" in f.text(g.clock.t)


def test_without_level_only_next(tmp_path):
    g = ExpGame(base="home", ratio=0.3, pct_per_min=0.5)
    f = LevelForecast(level=None, save_path=tmp_path / "l.json")
    run(g, f, 600)
    out = f.forecast(g.clock.t)
    true_next = (1 - g.ratio) / (0.5 / 100 / 60)
    assert out["next"] == pytest.approx(true_next, rel=0.10)
    assert out["to200"] is None
    assert "請在 config.yaml 設定 level" in f.text(g.clock.t)


def test_cursor_and_covered_moments_ignored(tmp_path):
    """滑鼠停在 EXP 條上、視窗被蓋住幾秒：那幾次讀不到就跳過，不影響預估。"""
    need = EXP_TABLE[196]
    rate = PER_HOUR / need * 100 / 60
    g = ExpGame(base="home", ratio=0.2, pct_per_min=rate,
                events=[(100, 108, "cursor"), (300, 306, "covered"), (400, 404, "black")])
    f = LevelForecast(level=196, save_path=tmp_path / "l.json")
    run(g, f, 600)
    true_next = (1 - g.ratio) * need / (PER_HOUR / 3600)
    assert f.forecast(g.clock.t)["next"] == pytest.approx(true_next, rel=0.10)


def test_death_exp_loss_resets_rate(tmp_path):
    f = LevelForecast(level=196)
    t = 0.0
    for i in range(200):
        f.add(0.5 + i * 0.0002, t); t += 2
    f.add(0.40, t)                         # 死掉掉經驗
    assert f.forecast(t)["next"] is None   # 重新觀察，不會算出奇怪的數字


def test_fmt_dur():
    assert fmt_dur(41 * 60) == "41 分鐘"
    assert fmt_dur(3 * 3600 + 5 * 60) == "3 小時 5 分"
    assert fmt_dur(50 * 3600) == "2 天 2 小時"


def test_real_game_constant_exp_across_level_up():
    """真實遊戲：每小時經驗固定（不是固定 %）。跨過升等前後都要算對。"""
    from autopot.expbar import fill_to_ratio, ratio_to_fill
    f = LevelForecast(level=196)
    w = 325                                   # 家裡畫面 EXP 框內部寬
    lv, exp, t = 196, 0.9 * EXP_TABLE[196], 0.0
    per_sec = PER_HOUR / 3600
    while t < 40 * 60:
        if exp >= EXP_TABLE[lv]:
            exp -= EXP_TABLE[lv]
            lv += 1
        shown = fill_to_ratio(ratio_to_fill(exp / EXP_TABLE[lv], w), w)    # 跟畫面一樣以 px 為單位
        f.add(shown, t)
        t += 2
        exp += per_sec * 2
    assert lv == 197 and f.level == 197
    out = f.forecast(t)
    assert out["per_hour"] == pytest.approx(PER_HOUR, rel=0.05)
    true_next = (EXP_TABLE[197] - exp) / per_sec
    assert out["next"] == pytest.approx(true_next, rel=0.05)
    true_200 = true_next + (EXP_TABLE[198] + EXP_TABLE[199]) / per_sec
    assert out["to200"] == pytest.approx(true_200, rel=0.05)


def test_rebirth_back_to_low_level_resumes_forecast(tmp_path):
    """200 等之後轉生（畫面上的等級變 61）→ 改用 61 等，繼續預估下次升等。"""
    from autopot.levelup import LevelForecast
    f = LevelForecast.load(200, tmp_path / "level.json")
    assert f.level == 200
    for i in range(3):
        f.see_level(61, 100.0 + i * 10)
    assert f.level == 61 and f.history == []
    for i in range(40):
        f.add(0.10 + i * 0.005, 1000.0 + i * 30)
    fc = f.forecast(1000.0 + 39 * 30)
    assert fc["next"] is not None and fc["next"] > 0
