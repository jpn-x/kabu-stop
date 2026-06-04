# セットアップ手順

## 1. GitHub リポジトリ作成

```bash
cd stop-gap-analysis
git init
git add .
git commit -m "initial commit"
gh repo create jpn-x/stop-gap-analysis --public --source=. --push
```

## 2. GitHub Pages 有効化

1. リポジトリ → Settings → Pages
2. Source: **Deploy from a branch**
3. Branch: `main` / `/ (root)`
4. Save → 数分後に `https://jpn-x.github.io/stop-gap-analysis/` で確認

---

## 3. バックフィル実行（過去データ一括取得）

ローカルで先に動作確認：

```bash
pip install -r requirements.txt
python analyzer.py --backfill
```

完了後 `data/gap_data.csv` が生成される。問題なければ push。

GitHub Actions から実行する場合:
- Actions タブ → Daily Gap Analysis → Run workflow
- `backfill: true` にチェックして実行

---

## 4. Google Sheets 連携（オプション）

### 4-1. Google Cloud でサービスアカウント作成

1. https://console.cloud.google.com/ → プロジェクト作成
2. API とサービス → ライブラリ → 「Google Sheets API」「Google Drive API」を有効化
3. IAM と管理 → サービスアカウント → 作成
4. キー → 新しいキーを追加 → JSON → ダウンロード

### 4-2. スプレッドシート作成・共有

1. Google Sheets で新規スプレッドシート作成
2. URL から Sheet ID をコピー:
   `https://docs.google.com/spreadsheets/d/【ここがID】/edit`
3. スプレッドシートを上記サービスアカウントのメールアドレスと共有（編集者権限）

### 4-3. GitHub Secrets に登録

リポジトリ → Settings → Secrets and variables → Actions → New repository secret

| Name | Value |
|------|-------|
| `GOOGLE_CREDENTIALS` | サービスアカウント JSON ファイルの中身（文字列全体） |
| `GOOGLE_SHEET_ID` | スプレッドシートの ID |

---

## 5. 自動実行スケジュール

`.github/workflows/daily.yml` により毎営業日 **17:30 JST** に自動実行。
手動実行は Actions タブ → Daily Gap Analysis → Run workflow。

---

## CSV カラム定義

| カラム | 説明 |
|--------|------|
| stop_date | ストップ高安をつけた日 |
| next_date | 翌営業日 |
| code | 銘柄コード |
| name | 銘柄名 |
| market | 上場市場 |
| stop_type | `stop_high`（高）/ `stop_low`（安） |
| prev_close | ストップ日終値（円） |
| next_open | 翌営業日始値（円）。寄らずの場合は空 |
| gap_yen | 始値 − 終値（円） |
| gap_pct | ギャップ率（%） |
| volume | 翌営業日出来高 |
| yorazu | `True` = 寄らず（出来高ゼロまたは始値ゼロ） |
