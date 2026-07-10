"""
株探 ストップ高・ストップ安 スクレイパー
毎日 16:30 JST（引け後）に実行する。

株探は GitHub Actions の IP をブロックするため、
Cloudflare Worker のプロキシ経由で取得する:
  https://stop-data.cadillac600.workers.dev/proxy?url=<kabutan URL>

  mode=3_1 → ストップ高
  mode=3_2 → ストップ安
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
import sys
import urllib.parse
from datetime import datetime, date, timezone, timedelta

import jpholiday

JST = timezone(timedelta(hours=9))
DATA_FILE = "data/stock_data.json"

# Cloudflare Worker プロキシ（環境変数で上書き可）
PROXY_BASE = os.environ.get(
    "KABUTAN_PROXY",
    "https://stop-data.cadillac600.workers.dev/proxy",
)

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    return s


def scrape_kabutan(session: requests.Session, mode: str) -> list[dict]:
    """
    mode='3_1' → ストップ高
    mode='3_2' → ストップ安

    株探 warning テーブル (table.stock_table) の1行は
    find_all(['th','td']) で 13 セル:
      [0] コード  [1] 銘柄名(th)  [2] 市場  [3] チャート  [4] （空）
      [5] 株価    [6] S印         [7] 前日比 [8] 変動率%  [9] ニュース
      [10] PER    [11] PBR        [12] 利回り
    """
    kabutan_url = f"https://kabutan.jp/warning/?mode={mode}"
    proxy_url = f"{PROXY_BASE}?url={urllib.parse.quote(kabutan_url, safe='')}"

    print(f"  Fetching {kabutan_url} (via proxy)")
    resp = session.get(proxy_url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="stock_table")
    if not table:
        print("  警告: stock_table が見つかりません")
        return []

    stocks = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) != 13:
            continue
        code = cells[0].get_text(strip=True)
        # コードは数字始まり（4桁 or 3桁+英字 例:264A）
        if not code[:1].isdigit():
            continue

        stocks.append({
            "code":   code,
            "name":   cells[1].get_text(strip=True),
            "market": cells[2].get_text(strip=True),
            "price":  cells[5].get_text(strip=True).replace(",", ""),
            "change": cells[7].get_text(strip=True).replace(",", ""),
            "rate":   cells[8].get_text(strip=True).replace("%", "").strip().lstrip("+"),
            "per":    cells[10].get_text(strip=True).replace("−", "").replace("－", ""),
            "pbr":    cells[11].get_text(strip=True).replace("−", "").replace("－", ""),
        })

    return stocks


def load_existing() -> dict:
    """
    JSONを読み込む。旧フォーマット（list）は新フォーマット（dict）に自動変換。
    新フォーマット: { "2026-04": [ {date, stop_high, stop_low}, ... ], ... }
    """
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        print("  旧フォーマット検出 → 新フォーマットへ変換")
        new_data: dict = {}
        for record in data:
            month_key = record["date"][:7]
            new_data.setdefault(month_key, []).append(record)
        for key in new_data:
            new_data[key].sort(key=lambda x: x["date"], reverse=True)
        return new_data
    return data


def save(all_data: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)


def main():
    now = datetime.now(JST)

    # TARGET_DATE は日付ラベルの上書きのみ（株探はリアルタイム板のため過去取得は不可）
    target = os.environ.get("TARGET_DATE", "").strip()
    if target:
        from datetime import date as date_type
        today = date_type.fromisoformat(target)
        date_str  = target
        month_key = target[:7]
    else:
        today = now.date()
        date_str  = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")

    print(f"=== 株データ取得: {date_str} ===")

    if today.weekday() >= 5 or jpholiday.is_holiday(today):
        print(f"  {date_str} は非営業日のためスキップ")
        sys.exit(0)

    session = make_session()

    try:
        print("ストップ高 取得中...")
        stop_high = scrape_kabutan(session, "3_1")
        print(f"  → {len(stop_high)} 銘柄")

        time.sleep(2)

        print("ストップ安 取得中...")
        stop_low = scrape_kabutan(session, "3_2")
        print(f"  → {len(stop_low)} 銘柄")

    except requests.RequestException as e:
        print(f"エラー: スクレイピング失敗 - {e}", file=sys.stderr)
        sys.exit(1)

    today_record = {
        "date":       date_str,
        "updated_at": now.isoformat(),
        "stop_high":  stop_high,
        "stop_low":   stop_low,
    }

    all_data = load_existing()
    all_data.setdefault(month_key, [])
    all_data[month_key] = [d for d in all_data[month_key] if d.get("date") != date_str]
    all_data[month_key].append(today_record)
    all_data[month_key].sort(key=lambda x: x["date"], reverse=True)

    save(all_data)
    print(f"完了: {DATA_FILE} に保存しました ({month_key} に {len(all_data[month_key])} 日分)")


if __name__ == "__main__":
    main()
