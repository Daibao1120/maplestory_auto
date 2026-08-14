# 在另一台機器上執行（掛機專用機）

把遊戲丟到一台舊筆電／舊電腦專門掛，主力機照常用。遊戲在那台是「唯一前景」，
焦點永遠不被搶，前景送鍵的方法就穩定了。以下是那台機器的安裝清單。

> ⚠️ 自動化操作線上遊戲違反遊戲 ToS、有**永久封號風險**，且會被反外掛偵測。
> 使用風險自負。建議先拿不心疼的小號驗證。

---

## 0. 前置需求

- [ ] **Windows**（送鍵用的是 Windows API，非 Windows 不能跑）
- [ ] **Python 3.10 以上** — 官網 <https://www.python.org/downloads/>
      安裝時**勾選「Add Python to PATH」**
- [ ] 那台機器裝好**楓之谷經典版**、能正常登入進到打怪地圖

---

## 1. 取得程式碼

用我給你的 `maplestory-classic-bot.bundle`（含完整 git 歷史）：

```bash
git clone maplestory-classic-bot.bundle maplestory-classic-bot
cd maplestory-classic-bot
```

沒有 git 也可以：直接把整個 `maplestory-classic-bot` 資料夾複製過去即可。

> 注意：`assets/templates/monsters/` 的鱷魚模板圖、`tests/data/` 的截圖是
> git-ignored，**不在 bundle 裡**。只跑下面「攻擊工具」用不到它們；要跑完整
> 影像辨識的話再另外把圖片複製過去。

---

## 2. 選一條路

### 路線 A：只要「連點攻擊 + 移動」工具（最簡單，**免安裝任何套件**）

`tools/hold_and_wiggle.py`、`tools/send_ctrl_to_maple.py` 只用到 Python 內建的
`ctypes`，**不需要 pip install 任何東西**。裝好 Python 就能跑。

### 路線 B：完整影像辨識 bot（讀 HP/怪物、自動決策）

需要額外套件：

```bash
pip install -r requirements.txt
```

（opencv-python、numpy、mss、pydirectinput、PyYAML）

---

## 3. 改兩個路徑（重要！）

`tools\run_hold_wiggle_admin.bat` 和 `tools\run_send_ctrl_admin.bat` 裡有**寫死的
路徑**，換機器一定要改：

1. `cd /d "D:\indexasia_David\maplestory-classic-bot"`
   → 改成新機器上這個資料夾的實際位置。
2. `"C:\Users\ibuzz\anaconda3\envs\linebot_RAG\python.exe"`
   → 改成新機器的 Python，通常直接寫 `python` 就好（前提是步驟 0 有勾 Add to PATH）：
   ```
   python tools\hold_and_wiggle.py --key ctrl --attack-interval 0.22 ...
   ```

用記事本開 `.bat` 改完存檔即可。（`.bat` 內容維持英文，中文會讓它執行失敗。）

---

## 4. 執行

**一定要用「系統管理員」身分跑**，否則遊戲（以管理員權限執行）會靜默丟掉送進去的
按鍵（UIPI 權限隔離）。兩個 `.bat` 已內建自動提權：**雙擊 → 接受 UAC 視窗**即可。

- `tools\run_hold_wiggle_admin.bat` — 連點 Ctrl 攻擊 + 每 45~90 秒原地小挪一下，
  焦點被搶走會自動切回楓之谷。
- `tools\run_send_ctrl_admin.bat` — 只送 Ctrl（可測 postmessage 背景送／sendinput 前景送）。

先確認找不找得到視窗、不送鍵：

```bash
python tools/send_ctrl_to_maple.py --list
```

---

## 5. 操作鍵

| 按鍵 | 作用 |
|------|------|
| （自動）每 0.22s | 點一下攻擊鍵 Ctrl |
| **方向鍵** | 你親自介入 → 腳本暫停、把控制權還你 |
| **Ctrl** | 暫停後按它 → 恢復掛機 |
| **F12** | 完全結束 |

常用參數（加在 `.bat` 那行 python 後面）：

- `--attack-interval 0.15` 打更快
- **`--move-time 0.18` 每步走多遠**：這是按住方向鍵的秒數。**太短（<0.1）角色只會
  轉身、不會真的走**，看起來像沒動 → 沒走就把它調大（0.2、0.25…）；走太遠就調小。
- **`--patrol-steps 2` 巡邏範圍**：往單邊最多走幾步就折返。平台很小 → 設小一點
  （**會掉下平台就調成 1**）。總移動範圍 ≈ move-time × patrol-steps。
- **`--face left` 固定攻擊方向**：攻擊永遠朝這一邊；巡邏往反方向走完後會**轉回這邊**
  再攻擊（不會射錯邊）。你朝右打就改 `--face right`。
- `--interval-min 5 --interval-max 12` 每隔幾秒挪一步（要更常換位就調小）
- `--no-move` 完全不動只打　`--no-refocus` 焦點被搶走不搶回（只暫停）
- `--window 新楓之谷` 視窗標題關鍵字（預設就是這個）

> 巡邏怎麼運作：角色往同方向一步一步走，走到 `patrol-steps` 步就折返往回走，
> 在「±步數」的小範圍內來回掃——位置真的有變（攻擊才有效），又不會走出小平台。

---

## 6. 常見狀況

| 症狀 | 原因 / 處理 |
|------|------------|
| 角色完全不動 | 沒以管理員權限跑（沒跳 UAC），或被反外掛擋 |
| 打一陣子就停 | 多半是 **MP 見底**（工具目前只送攻擊、不補魔）；換小號或手動補 |
| 找不到視窗 | 遊戲沒開，或 `--window` 關鍵字對不上實際標題 |
| 一直閃視窗 | 那是 Python/主控台視窗，正常；掛機專用機就別在上面做別的事 |
