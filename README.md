# KiCad 10 · 繁體中文 · Windows

**fork of [pcbcupid/Kicad-7-Series](https://github.com/pcbcupid/Kicad-7-Series)** · [English](README.en.md) · [v0.1.0](https://github.com/kongbai0123/Kicad-7-Series/releases/tag/v0.1.0)

以繁體中文、逐步操作與實際畫面，學習從原理圖到 PCB 製板輸出的完整流程。本 fork 在 [pcbcupid/Kicad-7-Series](https://github.com/pcbcupid/Kicad-7-Series) 的教材上，加入 **15 章 33 節的整節學習頁、90 個操作小步、第 00 章快捷鍵速查、每章練習題、截圖圖解、練習副本與共用進度**。

原教材使用 **KiCad 7**；本學習系統以 **Windows 與 KiCad 10.0.7 繁體中文介面**實作。需求：**Windows、Python 3.10 以上、KiCad**。變更見 [更新紀錄](CHANGELOG.md)。

![實際學習頁面：課程清單、逐項操作與圖上編號](docs/images/learning-page.jpg)

## 快速開始

需要 Windows、Python **3.10 以上**及 KiCad。學習入口只使用 Python 標準函式庫，不需安裝 Python 套件。自動 ERC、DRC 與封裝檢查會使用 KiCad 隨附的 `kicad-cli`。

```powershell
git clone https://github.com/kongbai0123/Kicad-7-Series.git
cd Kicad-7-Series
.\Start-Learning.cmd
```

也可以在檔案總管連按兩下根目錄的 **`Start-Learning.cmd`**。程式會啟動本機服務並開啟瀏覽器，預設網址為 `http://127.0.0.1:8765/`；若連接埠被占用，會尋找下一個可用位置。服務只監聽本機。

1. 在左側章節欄選一節，按 **開啟練習專案**。全部 15 課都會建立自己的副本。
2. 一節的小步排在同一頁：左邊照著做，右邊對照編號實拍；做完看「做完應該看到」。
3. 有自動檢查時可按 **先檢查一次**；「想一想」是選答，不會擋住記錄。
4. 實際完成後按 **我做完了，記錄這一步**。可以不照順序，也能直接跳到任何一節。
5. 卡住時，按 **複製接續訊息**貼入可存取本專案的 AI 助教對話，附上當前畫面，接著逐步問答。

問答引導在對話中進行，學習頁負責呈現課程及保存進度。本機頁面不需雲端帳號或 API 金鑰；AI 助教需能讀取本機專案並遵循通用的 `AGENTS.md`，不限定特定產品。完整操作方法見 [學習入口說明](learning/README.md)。停止本機服務可執行 **`Stop-Learning.cmd`**。

## 學習路線

第 00 章「常用功能與快捷鍵」是速查卡，依 KiCad 10 官方手冊核對，不計入進度。第 01～15 章對應原教材，每章分 2～4 節；最後一節附本課完成標準與選做練習題。

| 課程 | 主題 | 原教材 |
| --- | --- | --- |
| 01 | 入門與第一塊 LED 電路板 | [1_Introduction](1_Introduction/) |
| 02 | 介面、工具與畫面操作 | [2_User Interface](2_User%20Interface/) |
| 03 | 建立與核對 Diya 原理圖 | [3_Schematics](3_Schematics/) |
| 04 | 整理原理圖與圖框 | [4_Prettify_Schematics](4_Prettify_Schematics/) |
| 05 | 執行與處理 ERC | [5_Perform_ERC](5_Perform_ERC/) |
| 06 | 指定與核對元件封裝 | [6_Assign_Footprint](6_Assign_Footprint/) |
| 07 | 板層與疊構 | [7_PCB_STACKUP](7_PCB_STACKUP/) |
| 08 | 設計限制與網路類別 | [8_Constraints_And_Net_Classes](8_Constraints_And_Net_Classes/) |
| 09 | 配置元件與建立板框 | [9_PCB_Layout](9_PCB_Layout/) |
| 10 | 走線、換層與鋪銅 | [10_PCB_Routing](10_PCB_Routing/) |
| 11 | 執行與處理 DRC | [11_PCB_DRC](11_PCB_DRC/) |
| 12 | Gerber、鑽孔與製板 ZIP | [12_KiCad_Gerber_Generation](12_KiCad_Gerber_Generation/) |
| 13 | 專案符號庫 | [13_Create_Custom_Library](13_Create_Custom_Library/) |
| 14 | 封裝與 3D 對應 | [14_Create_Custom_Library_part-2](14_Create_Custom_Library_part-2/) |
| 15 | 生產前整理與版本比較 | [15_PCB_Optimization](15_PCB_Optimization/) |

原系列的共同專案是 ATtiny85 感光、感風的 Reactive / Digital Diya 電子燈。第 1 課練習 3V 電池、1k 電阻及 LED 圓形電路板；完成範例獨立放在 `docs/examples/`。各章教材維持上游原路徑，其中第 8 課使用獨立規則示範板，請依課程說明使用各自副本。

## 引導與進度

- **一節一頁**：一節的小步依序排在同一頁，每步是「一句重點 → 怎麼做 → 做完應該看到 → 常見失誤 → 找不到位置？」。
- **不限制跳轉**：任何一節都能直接打開，小步可不照順序記錄；側欄可收合成只剩編號。
- **實際畫面圖解**：課程步驟附 KiCad 10 截圖、位置編號與對應文字；放大後仍保留編號，並標明是操作實拍、學員截圖或原教材參考圖。
- **觀察與檢查**：以選答小問題協助辨認畫面，並從練習檔案檢查可驗證的結果。
- **證據分級**：學員確認、工具驗證、無法自動驗證與實體結果（製板、焊接、上電）分開記錄；練習題與主線分開計算。
- **共用進度**：網頁與教師命令列共用一份 JSON，以鎖定和 revision 檢查避免互相覆寫。
- **練習副本**：全部 15 課使用 `learning/workspaces/`，各課獨立操作。
- **接續教學**：學習紀錄與 [AGENTS.md](AGENTS.md) 保存引導方式，讓後續對話依最新進度接續。
- **重新學習**：任一課可「重新學習本課」，或一次「全部課程重新開始」；舊副本與進度封存到 `learning/archive/`，不會刪除。

新下載者會從 **第 1 課、全部未開始**起步。若沒有 `learning/progress.json`，程式會依 `progress.template.json` 自動建立空白進度；已有的個人進度會保留。個人進度、日誌、證據與工作副本均不納入 Git，公開範例不會自動匯入進度。

## 第 1 課實作成果

- [問答過程、卡關原因及解法範例](docs/examples/lesson-01.md)
- [圓形電路板 3D 畫面](learning/assets/lesson-01-round-board-3d.png)
- [範例設計、Gerber 與鑽孔輸出](docs/examples/led-board/)
- [範例 Introduction-Gerber.zip](docs/examples/led-board/gerber/Introduction-Gerber.zip)：7 個 Gerber、2 個鑽孔檔及 1 個 job 資訊檔，共 10 個檔案。

此成果已完成設計與輸出檢查；尚未記錄實際製板、焊接及點燈測試。

## 專案結構

```text
Kicad-7-Series/
├─ Start-Learning.cmd        本機學習入口
├─ Stop-Learning.cmd         停止本機服務
├─ AGENTS.md                 持久教學規範
├─ learning/
│  ├─ catalog.json           15 課、分節、操作步驟與練習題
│  ├─ reference.json         第 00 章快捷鍵速查
│  ├─ progress.template.json 空白進度範本
│  ├─ progress.json          個人進度，自動建立且不納入 Git
│  ├─ web/                   學習頁面（index 整節頁、legacy 舊版逐步頁）
│  ├─ checks.py              自動檢查
│  ├─ manage.py              教師進度工具
│  ├─ assets/                截圖與圖解
│  ├─ journal/               個人學習紀錄，不納入 Git
│  └─ workspaces/            本機練習副本，不納入 Git
├─ 1_Introduction/           上游原始入門教材
├─ 2_User Interface/ …       其他原教材課程
└─ docs/                    公開完成範例、產品畫面與原作者 README
```

請另外備份個人進度與練習成果。升級舊版本前，也應先備份舊版曾追蹤的進度與第一課設計；本次重構不會改寫先前的 Git 歷史。

## 檢查與維護

在根目錄執行：

```powershell
python -m unittest discover -s learning -p "test_*.py" -v
```

若有安裝 Node.js，也可以檢查前端 JavaScript 語法：

```powershell
node --check learning/web/sections.js
node --check learning/web/app.js
python learning/validate_repository.py
```

測試使用隔離資料，不會替學員完成課程。教師讀取進度使用 `python learning/manage.py status`；進度寫入方式見 [AGENTS.md](AGENTS.md)。

### 目前自動檢查的限制

[製板 ZIP 完整性驗證 #2](https://github.com/kongbai0123/Kicad-7-Series/issues/2) 仍待改善：目前只檢查 ZIP 可開啟與必要副檔名，不驗證圖層內容。[必要檢查無法執行時的判定 #1](https://github.com/kongbai0123/Kicad-7-Series/issues/1) 與 [放大圖片保留編號 #3](https://github.com/kongbai0123/Kicad-7-Series/issues/3) 已在本機版處理，尚未發布。

## 參與貢獻

請先閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。可使用 Issues 的問題回報與課程建議範本；GitHub Actions 會在 Windows 上執行 Python 3.10／3.13 測試與內容檢查。

## 原教材與授權

原教材由 **PCBCupid** 製作；本 fork 的逐步說明依實際操作整理，並非原影片逐字稿。

- [原作者儲存庫](https://github.com/pcbcupid/Kicad-7-Series)
- [原作者 README 完整內容](docs/UPSTREAM_README.md)
- [KiCad 教學播放清單](https://www.youtube.com/playlist?list=PLn6004q9oeqGl91KifK6xHGuqvXGb374G)
- [教學合輯](https://www.youtube.com/watch?v=KgmsvopC9Qk)
- [Reactive Diya 專案](https://github.com/Neutrino-1/SideQuest/tree/main/Reactive%20Diya)

沿用 [MIT License](LICENSE)，保留原作者著作權與授權條款。
