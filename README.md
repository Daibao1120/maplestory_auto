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
│   ├── main.py                 # 進入點：載入設定 → 啟動 engine
│   ├── capture/                # 螢幕擷取（mss / windows-capture 封裝）
│   ├── vision/                 # 模板匹配、小地圖定位、血條偵測
│   ├── input/                  # 鍵鼠模擬（pydirectinput / SendInput，含隨機延遲）
│   ├── engine/                 # 主迴圈引擎：串接 capture→vision→input
│   ├── routine/                # 路線資料模型與載入
│   ├── commands/               # command book：移動、攻擊、補水、換頻…
│   └── rune/                   # rune 偵測與解謎（介面 + TODO）
├── tools/
│   └── capture_template.py     # 小工具：框選截圖存成模板素材
├── assets/
│   └── templates/              # 模板素材（.gitignore 排除個人截圖）
└── tests/
    └── test_smoke.py           # 煙霧測試（import／基本邏輯）
```

---

## 安裝

建議使用 Python 3.10+ 與虛擬環境：

```bash
# 1) 建立並啟用虛擬環境（Windows PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 安裝相依套件
pip install -r requirements.txt
```

> 本骨架設計為「**缺套件也能 import**」：未安裝 `mss`／`opencv-python`／`pydirectinput` 時，模組仍可載入，只有在**實際使用**該功能時才會提示安裝。因此可以先跑測試、看架構，再逐步補齊環境。

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

## 開發狀態

這是一份**可運行的骨架**，重點在清楚的介面與擴充點；打怪 AI、rune 解謎、換頻流程等核心邏輯以 `TODO` 佔位，尚未完整實作。詳見各模組 docstring。

---

## 授權

僅供學習研究，無任何品質或適用性保證（AS IS）。使用前請再次閱讀上方免責聲明。
