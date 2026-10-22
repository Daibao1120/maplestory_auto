"""「快升等就停止自動狩獵」整個程式的模擬測試（跑真的 autostop.py 主迴圈，只是畫面和時間是假的）。

畫面用你的真實截圖：
  office = 公司電腦的方塊仔（1280x720，EXP 每分鐘漲約 4%，很快）
  home   = 家裡的主角（2764x1555，EXP 漲得慢）
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import autostop as A  # noqa: E402
import expsim  # noqa: E402
from autopot.autostop import AutoStop  # noqa: E402


class Timeout(Exception):
    pass


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    beeps = []
    monkeypatch.setattr(A, "beep", lambda n=3: beeps.append(n))

    def go(game, at=0.98, dry=False, max_min=180):
        clock = game.clock
        limit = clock.t + max_min * 60

        def sleep(s):
            clock.sleep(s)
            if clock.t > limit:
                raise Timeout()
        monkeypatch.setattr(A, "time", types.SimpleNamespace(time=clock.time, sleep=sleep))
        st = AutoStop(at=at)
        args = types.SimpleNamespace(at=at * 100, dry_run=dry, interval=2.0, no_topmost=False)
        try:
            result = A.loop(game, st, args)
        except Timeout:
            result = "timeout"
        return result, st, beeps
    return go


def assert_stopped_in_time(game, st, at):
    assert game.level == 0, "已經升級了，沒來得及停"
    assert not game.hunting, "自動狩獵還開著"
    first = game.clicks[0]
    px = 1 / (game.x1 - game.x0)          # EXP 條最細只讀得到 1px（公司電腦約 0.65%）
    thr = st.threshold()                  # 門檻太高時會自動降到「再 1px 就全滿」
    early = game.rate * 60 * 1.3          # EXP 漲很快時，會提早到「離升級還有 60 秒」就按
    assert min(thr, 1 - early) - 1.5 * px <= first[3] < 1.0, f"按的時機不對：EXP {first[3]:.2%}（門檻 {thr:.2%}）"
    b = game.btn
    assert b.x <= first[1] < b.x + b.w and b.y <= first[2] < b.y + b.h, "沒按在「自動狩獵」按鈕上"


# ---------- 正常情況 ----------
@pytest.mark.parametrize("base,rate", [("office", 4.0), ("office", 8.0), ("home", 0.5)])
def test_stops_before_level_up(run, base, rate):
    g = expsim.ExpGame(base=base, ratio=0.90, pct_per_min=rate)
    result, st, _ = run(g, at=0.98)
    assert result == "stopped"
    assert len(g.clicks) == 1
    assert_stopped_in_time(g, st, 0.98)


@pytest.mark.parametrize("at", [0.95, 0.97, 0.99])
def test_custom_threshold(run, at):
    g = expsim.ExpGame(base="office", ratio=0.90, pct_per_min=4.0)
    result, st, _ = run(g, at=at)
    assert result == "stopped"
    assert_stopped_in_time(g, st, at)


# ---------- 干擾 ----------
def test_cursor_on_exp_bar_while_crossing(run):
    """滑鼠剛好停在 EXP 條上（讀不到）的時候跨過門檻 → 移開後還是要停。"""
    g = expsim.ExpGame(base="office", ratio=0.95, pct_per_min=4.0, events=[(30, 60, "cursor")])
    result, st, _ = run(g, at=0.98)
    assert result == "stopped" and len(g.clicks) == 1
    assert_stopped_in_time(g, st, 0.98)


@pytest.mark.parametrize("kind", ["covered", "black"])
def test_window_covered_or_black_never_causes_wrong_click(run, kind):
    """遊戲被別的視窗蓋住／畫面變黑：不能亂按，看得到之後還是要及時停。"""
    ev = [(t, t + 6, kind) for t in range(0, 600, 10)]        # 一半以上的時間看不到
    g = expsim.ExpGame(base="office", ratio=0.93, pct_per_min=4.0, events=ev)
    result, st, _ = run(g, at=0.98)
    assert result == "stopped" and len(g.clicks) == 1
    assert_stopped_in_time(g, st, 0.98)


def test_first_click_missed_then_retries(run):
    """第一下沒按到（遊戲沒反應）→ EXP 還在漲 → 補按一次，還是要在升級前停下。"""
    g = expsim.ExpGame(base="office", ratio=0.95, pct_per_min=4.0, click_drop=1)
    result, st, beeps = run(g, at=0.97)
    assert len(g.clicks) == 2
    assert result == "stopped"
    assert g.level == 0 and not g.hunting


# ---------- 不該按的情況 ----------
def test_auto_hunt_already_off_never_clicks(run):
    """自動狩獵本來就關著（EXP 不動）→ 永遠不按，免得把它打開。"""
    g = expsim.ExpGame(base="office", ratio=0.985, pct_per_min=4.0, hunting=False)
    result, st, _ = run(g, at=0.98, max_min=20)
    assert result == "timeout" and g.clicks == []


def test_started_above_threshold_does_not_click_this_level(run):
    """一開始就已經超過門檻：沒看到它在漲 → 這一級不按（會升級），下一級到門檻再停。"""
    g = expsim.ExpGame(base="office", ratio=0.99, pct_per_min=4.0)
    result, st, _ = run(g, at=0.98, max_min=60)
    assert g.level == 1                       # 這一級沒攔（照設計）
    assert result == "stopped" and len(g.clicks) == 1
    assert g.clicks[0][3] >= min(st.threshold(), 1 - g.rate * 60 * 1.3) - 1.5 / (g.x1 - g.x0) and not g.hunting


def test_dry_run_never_clicks(run):
    g = expsim.ExpGame(base="office", ratio=0.95, pct_per_min=4.0)
    result, st, _ = run(g, at=0.98, dry=True)
    assert result == "dry" and g.clicks == [] and g.hunting


# ---------- 整個程式（main）----------
def test_main_sets_and_restores_topmost(monkeypatch, tmp_path):
    g = expsim.ExpGame(base="office", ratio=0.96, pct_per_min=4.0)
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "pick_window", lambda k, p: g)
    monkeypatch.setattr(A, "beep", lambda n=3: None)
    monkeypatch.setattr(A, "time", types.SimpleNamespace(time=g.clock.time, sleep=g.clock.sleep))
    monkeypatch.setattr(sys, "argv", ["autostop.py", "--at", "98"])
    A.main()
    assert g.topmost == [True, False]
    assert len(g.clicks) == 1 and not g.hunting and g.level == 0
    assert (tmp_path / "data" / "exp_debug.png").exists()


def test_main_restores_topmost_on_ctrl_c(monkeypatch, tmp_path):
    g = expsim.ExpGame(base="office", ratio=0.5, pct_per_min=1.0)
    monkeypatch.setattr(A, "ROOT", tmp_path)
    monkeypatch.setattr(A, "pick_window", lambda k, p: g)
    n = {"i": 0}

    def sleep(s):
        n["i"] += 1
        g.clock.sleep(s)
        if n["i"] > 20:
            raise KeyboardInterrupt
    monkeypatch.setattr(A, "time", types.SimpleNamespace(time=g.clock.time, sleep=sleep))
    monkeypatch.setattr(sys, "argv", ["autostop.py"])
    with pytest.raises(KeyboardInterrupt):
        A.main()
    assert g.topmost == [True, False]


# ---------- 隨機壓力測試：各種干擾混在一起，跑 100 種組合 ----------
@pytest.mark.parametrize("seed", range(100))
def test_random_interference(run, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    base = "office" if seed % 3 else "home"
    rate = float(rng.uniform(2, 8)) if base == "office" else float(rng.uniform(0.3, 1.0))
    at = float(rng.choice([0.96, 0.97, 0.98]))
    ev, t = [], 0
    while t < 3000:                               # 隨機出現：游標、被蓋住、黑畫面
        t += int(rng.integers(10, 60))
        # 遊戲固定在最上層，所以看不到的時間都很短（切視窗、滑鼠經過）。
        # 更長的「完全看不到」又剛好碰上第一下沒按到，任何程式都救不了（升級前根本看不到畫面）。
        ev.append((t, t + int(rng.integers(2, 9)), str(rng.choice(["cursor", "covered", "black"]))))
    g = expsim.ExpGame(base=base, ratio=float(rng.uniform(0.80, at - 0.02)), pct_per_min=rate,
                       events=ev, click_drop=int(rng.random() < 0.3), seed=seed)
    result, st, _ = run(g, at=at, max_min=240)
    assert g.level == 0, f"seed {seed}: 升級了（{result}）"
    assert not g.hunting, f"seed {seed}: 自動狩獵還開著（{result}）"
    assert 1 <= len(g.clicks) <= 2



def test_exp_still_rises_a_bit_right_after_stop(run):
    """實測 10/20 12:56：按停止後角色還會把怪打完，EXP 多漲一點 → 不能誤判成「沒按到」又按一次（會把自動狩獵打開）。"""
    for coast in (3, 8, 12):
        g = expsim.ExpGame(base="office", ratio=0.25, pct_per_min=4.0, coast=coast)
        result, st, _ = run(g, at=0.30)
        assert len(g.clicks) == 1, f"coast {coast}s: 多按了 {len(g.clicks) - 1} 下"
        assert not g.hunting and result == "stopped"


def test_real_log_1020_1256_replay(run):
    """照你 12:54 的實際紀錄：22.8% 開始、約每分鐘 4%、門檻 30%。"""
    g = expsim.ExpGame(base="office", ratio=0.228, pct_per_min=3.9, coast=8)
    result, st, _ = run(g, at=0.30)
    assert len(g.clicks) == 1 and not g.hunting and g.level == 0
