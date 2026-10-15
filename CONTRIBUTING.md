# 參與貢獻

歡迎修正繁體中文教學、補充英文說明，以及改善本機學習入口。先在 Issues 說明課程與步驟編號，再提出範圍清楚的變更。

## 開發與檢查

需要 Python 3.10 以上。正式啟動入口支援 Windows；Node.js 只用於前端語法檢查，不是學員使用頁面的必要條件。

```powershell
python -m unittest discover -s learning -p "test_*.py" -v
node --check learning/web/app.js
python learning/validate_repository.py
```

GitHub Actions 在 Windows、Python 3.10 與 3.13 執行以上檢查。測試資料必須隔離，不可替學員確認課程或重設其進度。

## 教學內容

- 遵循 [AGENTS.md](AGENTS.md)：一次一小步、1 至 3 個操作，並提供可見的成功判準。
- 截圖須是實際 KiCad 畫面。以臨時教材副本擷取，逐一核對編號座標，不編造成功畫面。
- 自動檢查只讀學員副本；需要工具寫入時，先建立臨時副本。
- 原教材目錄維持上游路徑，包含含空格的目錄名稱。完成範例放在 `docs/examples/`，不要覆蓋教材。
- 新增課程欄位時同步更新空白範本、測試與中英文說明。

## 個人資料與版本控制

`learning/progress.json`、`journal/`、`evidence/`、`workspaces/`、歷史與執行日誌均為本機資料，不應加入提交。新安裝會由 `progress.template.json` 建立空白進度，已有進度不會被範本覆蓋。

公開教學範例可放到 `docs/examples/`，但須移除私人絕對路徑並清楚標示為範例。截圖附到 Issue 前，也請先檢查是否含私人資訊。

## 相容性與版本

學習入口只使用 Python 標準函式庫。變更時維持 Python 3.10 相容，不加入特定 AI 產品的必要依賴。能讀取本機專案並遵循 `AGENTS.md` 的 AI 助教都可接續教學。

發布前更新 `CHANGELOG.md`，執行測試並確認 CI 成功後建立版本 tag。原教材沿用 MIT 授權，請保留出處與授權條款。
