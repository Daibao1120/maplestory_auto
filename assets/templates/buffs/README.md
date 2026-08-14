# buffs／要自動點掉的 buff 圖示

把「**不想要的 buff 圖示**」截圖放進這個資料夾，腳本偵測到畫面右上角出現
同樣的圖示時，會自動用**滑鼠右鍵**點掉（點完游標會移回原位）。

典型例子：別人丟給你的「**速度激發**」——移速變快之後，腳本校準好的步伐
全部會走過頭，一動就掉出平台，必須馬上點掉。

## 怎麼截圖

1. 遊戲裡等該 buff 出現在右上角。
2. 用內建工具框選圖示本體（只框圖示、不要框到背景）：

   ```bash
   python tools/capture_template.py --name speed_boost --out assets/templates/buffs
   ```

   或自己截圖裁切後存成 PNG 放進來，檔名隨意。

## 注意

- **這裡的每一張圖都會被視為「要點掉的 buff」**，不要放你想保留的 buff。
- 可放多張（不同 buff、或同一 buff 的不同顯示狀態）。
- 對應設定在 `config/settings.yaml` 的 `vision.buff_dispel`（門檻、檢查頻率、ROI）。
- 圖片不進版控（遊戲美術素材請自行保存）。
