"""
ストップ高安 翌営業日 寄り付きギャップ分析スクリプト

使い方:
  python analyzer.py              # 前営業日分のみ処理（毎日自動実行用）
  python analyzer.py --backfill   # stock_data.json の全履歴を一括処理
"""

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jpholiday
import pandas as pd
import requests
import yfinance as yf

JST = timezone(timedelta(hours=9))
STOP_DATA_URL = None  # ローカルファイルから読み込む
STOP_DATA_PATH = Path(__file__).parent / "data" / "stock_data.json"
DATA_DIR = Path("data")
GAP_CSV = DATA_DIR / "gap_data.csv"

CSV_COLUMNS = [
    "stop_date",    # ストップ高安日
    "next_date",    # 翌営業日
    "code",         # 銘柄コード
    "name",         # 銘柄名
    "market",       # 市場
    "stop_type",    # stop_high / stop_low
    "prev_close",   # ストップ日終値（分析基準）
    "next_open",    # 翌営業日始値
    "gap_yen",      # 始値ギャップ（円）
    "gap_pct",      # 始値ギャップ（%）
    "next_high",    # 翌営業日高値
    "next_low",     # 翌営業日安値
    "next_close",   # 翌営業日終値（当日終値）
    "range_yen",    # 前日終値→当日終値 値幅（円）
    "range_pct",    # 前日終値→当日終値 値幅（%）
    "volume",       # 翌営業日出来高
    "yorazu",       # 寄らずフラグ（True=寄らず）
    "reason",       # ストップ高安の理由（カブタンニュース見出し）
]


# ── 営業日ユーティリティ ──────────────────────────────────────────────────────

def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and not jpholiday.is_holiday(d)


def next_business_day(d: date) -> date:
    nd = d + timedelta(days=1)
    while not is_business_day(nd):
        nd += timedelta(days=1)
    return nd


def prev_business_day(d: date) -> date:
    pd_ = d - timedelta(days=1)
    while not is_business_day(pd_):
        pd_ -= timedelta(days=1)
    return pd_


# ── データ取得 ────────────────────────────────────────────────────────────────

def fetch_stop_data() -> dict:
    """ローカルの stock_data.json を読み込む"""
    print("stock_data.json 読み込み中...")
    with open(STOP_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_price_data(code: str, target_date: date) -> dict | None:
    """
    yfinance で指定銘柄・指定日の始値・出来高を取得。
    東証銘柄は code + '.T' で取得（例: 3103.T, 281A.T）。

    注意: yfinance は日本株で T+1 遅延が発生することがある。
    返されたデータの日付が target_date と一致するか検証する。
    """
    ticker_str = f"{code}.T"
    start = target_date.strftime("%Y-%m-%d")
    end = (target_date + timedelta(days=2)).strftime("%Y-%m-%d")  # +2日で確実に取得

    try:
        ticker = yf.Ticker(ticker_str)
        hist = ticker.history(start=start, end=end, auto_adjust=True)

        if hist.empty:
            return None

        # target_date (JST) と一致する行を探す
        target_str = target_date.strftime("%Y-%m-%d")
        matched = None
        for idx, row in hist.iterrows():
            # yfinance のインデックスは timezone-aware (Asia/Tokyo) の場合がある
            row_date = idx.date() if hasattr(idx, "date") else idx
            if str(row_date) == target_str:
                matched = row
                break

        if matched is None:
            print(f"    ! {code}: {target_str} のデータが見つからない (返却: {[str(i.date()) for i in hist.index]})")
            return None

        open_price  = float(matched["Open"])
        high_price  = float(matched["High"])
        low_price   = float(matched["Low"])
        close_price = float(matched["Close"])
        volume      = int(matched["Volume"])
        yorazu = volume == 0 or open_price == 0

        return {
            "open":   round(open_price,  1) if open_price  else None,
            "high":   round(high_price,  1) if high_price  else None,
            "low":    round(low_price,   1) if low_price   else None,
            "close":  round(close_price, 1) if close_price else None,
            "volume": volume,
            "yorazu": yorazu,
        }
    except Exception as e:
        print(f"    ! {code} 取得失敗: {e}")
        return None


def fetch_day_reasons(nbd: date, sample_code: str) -> dict:
    """
    カブタン「前日に動いた銘柄 part2」記事から {code: reason} を一括取得。
    翌営業日(nbd)に公開される記事を対象とし、1日分まとめて取得することで
    HTTP リクエスト数を最小化する。
    """
    import re as _re
    try:
        # sample_code のニュース一覧から nbd 公開の「前日に動いた銘柄 part2」を探す
        list_url = f"https://kabutan.jp/stock/news/?code={sample_code}"
        resp = requests.get(
            list_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8,
        )
        resp.raise_for_status()
        text = resp.content.decode("utf-8", errors="ignore")

        nbd_str = nbd.strftime("%Y-%m-%d")
        rows = _re.findall(r"<tr[^>]*>(.*?)</tr>", text, _re.DOTALL)
        article_url = None
        for row in rows:
            if nbd_str in row and "前日に動いた銘柄" in row:
                # href="/stock/news?code=XXXX&b=nYYYYMMDDZZZZ" 形式
                m = _re.search(r'href="(/stock/news\?[^"]+&b=(n\d+))"', row)
                if m:
                    # 記事本文の直接URL: /news/marketnews/?b=nXXX
                    b_id = m.group(2)
                    article_url = f"https://kabutan.jp/news/marketnews/?b={b_id}"
                    break

        if not article_url:
            print("    → 「前日に動いた銘柄 part2」記事が見つからず（理由なし）")
            return {}

        print(f"    → 理由記事取得: {article_url}")
        time.sleep(0.3)
        resp2 = requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=8,
        )
        resp2.raise_for_status()
        text2 = resp2.content.decode("utf-8", errors="ignore")

        # 実際の HTML 構造:
        # 名前&lt;<a href="/stock/?code=CODE">CODE</a>&gt; PRICE CHANGE<br />
        # 理由テキスト。<br />
        entries = _re.findall(
            r'href="/stock/\?code=(\w+)"[^>]*>[^<]+</a>&gt;[^<\n]*<br\s*/>\s*([^<\n。．]{4,})[。．]?<br',
            text2,
        )
        result = {}
        for code, reason in entries:
            clean = reason.strip()
            if clean:
                result[code] = clean[:120]

        print(f"    → 理由取得完了: {len(result)} 銘柄")
        return result

    except Exception as e:
        print(f"    ! fetch_day_reasons 失敗: {e}")
        return {}


# ── CSV 操作 ──────────────────────────────────────────────────────────────────

def load_existing() -> pd.DataFrame:
    if GAP_CSV.exists():
        return pd.read_csv(GAP_CSV, dtype=str)
    return pd.DataFrame(columns=CSV_COLUMNS)


def save_csv(df: pd.DataFrame):
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(GAP_CSV, index=False, encoding="utf-8-sig")


# ── Google Sheets 更新（オプション） ──────────────────────────────────────────

def update_google_sheets(df: pd.DataFrame):
    """
    環境変数 GOOGLE_CREDENTIALS（JSON文字列）と GOOGLE_SHEET_ID が
    設定されている場合のみ実行。
    """
    import os
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")

    if not creds_json or not sheet_id:
        print("Google Sheets の環境変数未設定 → スキップ")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)

        worksheet = sh.get_worksheet(0)
        worksheet.clear()

        # ヘッダー + データ
        header = list(df.columns)
        rows = df.fillna("").values.tolist()
        worksheet.update([header] + rows)
        print(f"Google Sheets 更新完了: {len(rows)} 行")

    except Exception as e:
        print(f"⚠ Google Sheets 更新失敗: {e}")


# ── メイン処理 ────────────────────────────────────────────────────────────────

def process_day(
    stop_date_str: str,
    day_data: dict,
    existing_keys: set,
    today: date,
) -> list[dict]:
    """1日分のストップ高安銘柄を処理してギャップデータのリストを返す"""
    stop_date = date.fromisoformat(stop_date_str)

    # 祝日・休日のデータはストップ高安が発生しないためスキップ
    # （stopstock.json は祝日でも前日と同じ銘柄を重複掲載する場合がある）
    if not is_business_day(stop_date):
        print(f"  {stop_date_str} は非営業日（祝日・休場）→ スキップ")
        return []

    nbd = next_business_day(stop_date)

    # 翌営業日がまだ来ていない場合はスキップ
    if nbd > today:
        return []

    # 新規処理対象を先にリストアップ
    new_stocks = [
        (st, s)
        for st in ("stop_high", "stop_low")
        for s in day_data.get(st, [])
        if f"{stop_date_str}_{s['code']}_{st}" not in existing_keys
    ]
    if not new_stocks:
        return []

    # 1日分の理由を一括取得（「前日に動いた銘柄 part2」記事）
    # 複数コードを試してどれかで記事が見つかれば OK
    day_reasons: dict = {}
    for _, _s in new_stocks[:5]:
        day_reasons = fetch_day_reasons(nbd, _s["code"])
        if day_reasons:
            break

    results = []
    for stop_type, stock in new_stocks:
        code = stock["code"]

        prev_close_str = stock.get("price", "").replace(",", "")
        try:
            prev_close = float(prev_close_str)
        except ValueError:
            continue

        label = "ストップ高" if stop_type == "stop_high" else "ストップ安"
        print(f"  [{label}] {code} {stock['name']}  終値={prev_close}円 → {nbd} 始値取得中...")

        price_data = fetch_price_data(code, nbd)
        time.sleep(0.4)  # レート制限対策

        if price_data and price_data["open"]:
            next_open  = price_data["open"]
            next_high  = price_data["high"]
            next_low   = price_data["low"]
            next_close = price_data["close"]
            gap_yen   = round(next_open  - prev_close, 1)
            gap_pct   = round((next_open  - prev_close) / prev_close * 100, 2)
            range_yen = round(next_close - prev_close, 1) if next_close else None
            range_pct = round((next_close - prev_close) / prev_close * 100, 2) if next_close else None
            volume    = price_data["volume"]
            yorazu    = price_data["yorazu"]
        else:
            next_open  = None
            next_high  = None
            next_low   = None
            next_close = None
            gap_yen    = None
            gap_pct    = None
            range_yen  = None
            range_pct  = None
            volume     = price_data["volume"] if price_data else None
            yorazu     = True

        reason = day_reasons.get(code, "")

        results.append({
            "stop_date":  stop_date_str,
            "next_date":  nbd.isoformat(),
            "code":       code,
            "name":       stock["name"],
            "market":     stock["market"],
            "stop_type":  stop_type,
            "prev_close": prev_close,
            "next_open":  next_open,
            "gap_yen":    gap_yen,
            "gap_pct":    gap_pct,
            "next_high":  next_high,
            "next_low":   next_low,
            "next_close": next_close,
            "range_yen":  range_yen,
            "range_pct":  range_pct,
            "volume":     volume,
            "yorazu":     yorazu,
            "reason":     reason,
        })

    return results


def main():
    backfill = "--backfill" in sys.argv
    today = datetime.now(JST).date()

    all_stop_data = fetch_stop_data()

    # 全日付をフラットなリストに展開
    all_days: list[dict] = []
    for month_list in all_stop_data.values():
        for day_data in month_list:
            all_days.append(day_data)

    all_days.sort(key=lambda x: x["date"])  # 古い順

    if not backfill:
        # 通常モード: 翌営業日が今日以前で CSV 未収録の日をすべて処理
        # （祝日明けに前日分が抜ける問題を防ぐ）
        all_days = [
            d for d in all_days
            if next_business_day(date.fromisoformat(d["date"])) <= today
        ]
        print(f"対象: {len(all_days)} 日分（翌営業日≦今日）")
    else:
        print(f"バックフィルモード: {len(all_days)} 日分を処理")

    existing_df = load_existing()
    existing_keys: set[str] = set()
    if not existing_df.empty:
        existing_keys = {
            f"{r.stop_date}_{r.code}_{r.stop_type}"
            for r in existing_df.itertuples()
            if not (r.yorazu and pd.isna(r.next_open))
        }

    all_results: list[dict] = []
    for day_data in all_days:
        date_str = day_data["date"]
        stop_high_cnt = len(day_data.get("stop_high", []))
        stop_low_cnt = len(day_data.get("stop_low", []))
        print(f"\n=== {date_str}  高:{stop_high_cnt} 安:{stop_low_cnt} ===")
        all_results.extend(process_day(date_str, day_data, existing_keys, today))

    if not all_results:
        print("\n新規データなし。終了。")
        return

    new_df = pd.DataFrame(all_results, columns=CSV_COLUMNS)
    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["stop_date", "code", "stop_type"], keep="last")
    combined = combined.sort_values(["stop_date", "code"], ascending=[False, True])
    save_csv(combined)

    print(f"\n完了: {len(all_results)} 件追加 -> {GAP_CSV}  (合計: {len(combined)} 件)")

    update_google_sheets(combined)


if __name__ == "__main__":
    main()
