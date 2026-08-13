# 楓之谷經典版 圖色辨識自動化腳本（maplestory-classic-bot）

一套以**純電腦視覺（Computer Vision）** 為基礎的《楓之谷 經典版》自動化腳本**骨架**。不讀取、不修改遊戲記憶體，僅透過「**截圖 → 影像辨識 → 模擬鍵鼠**」的方式運作。

> ⚠️ **重要免責聲明（請務必詳讀）**
>
> 本專案僅供**電腦視覺與自動化技術之學習研究**用途。使用任何自動化腳本操作線上遊戲，**幾乎必然違反遊戲服務條款（Terms of Service, ToS）**，並可能導致帳號遭到**永久停權（封號）** 或其他處分。
>
> 作者與貢獻者**不對**任何因使用本專案而造成的帳號損失、資料損失或其他任何後果負責。**是否使用、如何使用，風險由使用者自行承擔。** 若你不接受此條件，請勿使用本專案。
>
> 本專案**不附帶任何遊戲美術素材**；所有模板圖片需由使用者自行從自己的遊戲畫面擷取。

---

## 設計理念

架構參考兩個開源專案的精神：

- **MapleStoryAutoLevelUp**（純 CV 流程）：`截圖 →（小地圖定位 ＋ 血條找角色）→ 模板匹配打怪 → 自動解 rune／喝水／換頻`。全程不碰記憶體。
- **Auto Maple**（三層解耦架構）：
  - **routine（路線）** — 定義角色要走的路徑，以及在每個定位點要執行哪些動作。
  - **command book（動作指令）** — 把「移動／攻擊／補水／換頻」等動作抽象成可組合、可替換的指令。
  - **engine（執行引擎）** — 主迴圈，負責串接「擷取 → 辨識 → 輸入」。

三層解耦的好處：換職業／換地圖時，只需改 routine 與 command book，引擎與辨識層可重用。

### 資料流

```
┌──────────┐   frame   ┌──────────┐   偵測結果   ┌──────────┐   按鍵    ┌──────────┐
│ capture  │ ────────▶ │  vision  │ ──────────▶ │  engine  │ ───────▶ │  input   │
│ 螢幕擷取 │           │ 影像辨識 │             │ 決策迴圈 │          │ 鍵鼠模擬 │
└──────────┘           └──────────┘             └────┬─────┘          └──────────┘
                                                     │ 查表
                                        ┌────────────┴────────────┐
                                        ▼                         ▼
                                  routine（路線）        commands（command book）
```

---

## 專案結構

```
maplestory-classic-bot/
├── README.md                   # 本說明檔
├── requirements.txt            # 相依套件
├── .gitignore
├── config/
│   ├── settings.example.yaml   # 設定範例（視窗標題、按鍵、閾值…）
│   └── routines/
│       └── example.yaml        # 範例路線
├── src/
│   ├── main.py                 # 進入點：載入設定 → 啟動 engine（含 --dry-run/--demo-image）
│   ├── capture/                # 螢幕擷取（mss / windows-capture 封裝）
│   ├── vision/                 # 模板匹配、小地圖定位、血條讀值、怪物偵測、合成場景
│   ├── input/                  # 鍵鼠模擬（pydirectinput / SendInput，含隨機延遲）
│   ├── engine/                 # 主迴圈：感知→(補水/rune/打怪/巡邏)
│   ├── routine/                # 路線資料模型與載入
│   ├── commands/               # command book（移動/攻擊/走近…）＋ combat 攻擊決策
│   └── rune/                   # rune 偵測與解謎（介面 + TODO）
├── tools/
│   └── capture_template.py     # 小工具：框選截圖存成模板素材
├── assets/
│   └── templates/
│       └── monsters/           # 放你自己的鱷魚截圖（見該資料夾 README；圖片不進版控）
└── tests/
    ├── test_smoke.py           # 煙霧測試（import／基本邏輯）
    ├── test_combat.py          # 攻擊決策（純函式）
    └── test_vision_functional.py  # 以合成影像實跑 OpenCV（模板/小地圖/血條/怪物/整合）
```

---

## Windows 快速上手（實際執行環境）

最終在 Windows 上執行（需要 `pydirectinput` 送鍵、擷取實際遊戲畫面）。以下 PowerShell 步驟可直接照做：

```powershell
# 1) 進入專案資料夾
cd D:\indexasia_David\maplestory-classic-bot

# 2) 建立並啟用虛擬環境（需 Python 3.10+）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3) 安裝相依套件（含 Windows 專屬的 pydirectinput）
pip install -r requirements.txt

# 4) 複製設定範例，之後再依你的視窗標題／按鍵微調
copy config\settings.example.yaml config\settings.yaml

# 5) 空轉測試：用合成畫面、不送實體按鍵，把主迴圈跑 4 圈
python -m src.main --dry-run --max-loops 4

# 6) 跑測試
python -m pytest -q
```

`--dry-run` 會使用合成畫面、只印出動作而不真的送鍵，所以**不必開遊戲、也不會亂點你的畫面**，很適合先確認流程是否正確。確認無誤後，開好遊戲、把視窗標題與按鍵填進 `settings.yaml`，再拿掉 `--dry-run` 正式執行：

```powershell
python -m src.main --config config\settings.yaml
```

> - 本骨架「**缺套件也能 import**」：未裝 `mss`／`opencv-python`／`pydirectinput` 時模組仍可載入，只有實際使用時才提示安裝，因此可先跑測試、看架構再補環境。
> - `requirements.txt` 預設**不含** `windows-capture`（進階選用擷取後端）；預設的 `mss` 就能運作。
> - 在 Linux／macOS 只能安裝跨平台套件（`opencv-python-headless`、`numpy`、`PyYAML`、`mss`、`pytest`）來跑測試與 `--dry-run`；送鍵與真實擷取需在 Windows 上驗證。

---

## 使用方式

1. **複製設定檔**：把 `config/settings.example.yaml` 複製成 `config/settings.yaml`，依你的遊戲視窗標題、按鍵綁定與畫面解析度調整。
2. **建立模板素材**：經典版是高解析重繪，模板**必須用你自己遊戲畫面的實際截圖重建**。用內建工具框選存檔：

   ```bash
   python tools/capture_template.py --name slime --out assets/templates
   ```
3. **編輯路線**：參考 `config/routines/example.yaml` 定義走位與動作。
4. **啟動**：

   ```bash
   python -m src.main --config config/settings.yaml
   ```
   預設熱鍵（可於設定檔調整）：`F12` 緊急停止。

---

## 實戰設定：戰火之地・沼澤地 I（弓箭手打鱷魚）

`config/settings.example.yaml` 的預設值取自這張地圖的**實際截圖**校準：

- **視窗**：標題「新楓之谷」（完整為「新楓之谷 · 經典版」）。
- **地圖**：戰火之地：沼澤地 I（Map ID 107000000），寬幅、大致平坦、橫向動線長 → 移動以**地面左右橫向巡邏**為主，到折返點前掉頭避免走進水裡。
- **目標怪**：鱷魚 Croco Lv32（HP 1200 / 移速 −40 很慢 / 迴避 12 / 可擊退）。好瞄準、可風箏；HP 高，需要**對同側持續輸出到怪消失**。
- **攻擊（弓箭手・遠程定點）**：偵測到鱷魚 → **面向怪較多的一側** → 站定放**主力技**連射；一次濺射／穿透打一排。
  - 主力技（Lv30–40 二轉）：獵人(弓)＝**爆炸箭**（範圍濺射＋約60%暈眩）；弩手(弩)＝**貫穿箭**（直線穿透多隻＋冰凍）。攻擊鍵預設 **Ctrl**（`combat.attack_key`）。
  - 「箭雨／亂射」是 3 轉技能，這個等級沒有，預設不使用。
- **命中提醒**：鱷魚命中需求約 44，DEX／命中不足會 **miss**——畫面偵測得到、但實際打不中，請先顧好命中再讓腳本連續輸出。
- **寵物排除**：畫面中的**白色兔子是寵物**，會跟在角色旁。怪物偵測用「鱷魚模板」比對，天生不會把兔子當鱷魚；另可用 `vision.monster.roi` 限定平台帶再保險。
- **兩平台輪流清怪**（`combat.platforms`）：左、右兩個平台輪流巡邏。箭是水平飛的，用 `combat.attack_y_band` 只打「與角色同高度帶」的怪（同平台 dy≈+26、另一平台 dy≈+84，[0, 60] 剛好分開）；目前平台連續 `empty_loops` 圈沒可打的怪就走過去、到平台下方邊走邊跳上去，卡太久（`max_move_loops`）會放棄回守。平台座標用小地圖 (x, y) 判定，校準法見 settings 註解。

感知資料流：`小地圖黃點→玩家座標`、`HP/MP 條 ROI→剩餘%`、`鱷魚模板匹配→鱷魚清單`，再交給攻擊/巡邏決策。dry-run（`--demo-image` 餵真截圖）實測輸出範例：

```
[loop 1] 視窗:「新楓之谷」 | 玩家(小地圖):(140, 12)  HP:76%  MP:36%  鱷魚:2
        ↳ 打怪：偵測到 2 隻 → 最近(1070,514) dx=385 → 面向right → 放技能 ctrl×3
```

## 校準 HSV / ROI（換解析度或帳號務必重做）

`settings.example.yaml` 內所有 ROI 都是「相對遊戲視窗左上角」的像素，參考視窗約 1370×865（含標題列）。你的解析度/視窗大小不同時請重新校準：

1. **截一張你自己的遊戲畫面**（同你平常玩的視窗大小）。
2. **ROI（矩形區域）**：用小畫家/看圖軟體讀出「小地圖地圖區」「HP 條」「MP 條」的 `[left, top, width, height]`，填入 `vision.minimap.roi` 與 `vision.health_bar.hp_bar_roi / mp_bar_roi`。
   - 小技巧：HP/MP 條寬度可用「已知數值反推」——例如 HP 顯示 1010/1322≈76%，量出紅色填充寬度 ÷ 0.76 就是條總寬。
3. **HSV 顏色**：若小地圖黃點抓不到、或血條讀值不準，微調對應的 `*_color_lower/upper`（OpenCV 的 H 是 0–179）。
4. **怪物模板**：把鱷魚截圖放到 `assets/templates/monsters/`（多補不同動作/方向更準），需要時調 `vision.monster.match_threshold`（真實場景常用 0.55~0.70）。
5. **巡邏邊界**：在遊戲裡走到平台左右端，看小地圖玩家 x 值，填 `combat.patrol_left_x / patrol_right_x`（設在水邊之前，避免掉下去）。

## 開發狀態

**已實作並用合成影像＋真實截圖驗證**：小地圖玩家定位、HP/MP 讀值、鱷魚多模板偵測＋NMS、弓箭手攻擊決策、**兩平台輪流清怪（同高度帶過濾＋跳上平台＋防卡死）**、平台巡邏、整條 dry-run 感知→決策。**仍為 TODO**：`windows-capture` 後端、以 ctypes 定位遊戲視窗、rune 解謎的箭頭辨識。詳見各模組 docstring。

---

## 授權

僅供學習研究，無任何品質或適用性保證（AS IS）。使用前請再次閱讀上方免責聲明。
