"""
kabudragon ストップ高・ストップ安 スクレイパー
毎日 16:10 JST 以降に実行する
（kabutan は GitHub Actions IP をブロックするため kabudragon に切り替え）
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
import sys
from datetime import datetime, date, timezone, timedelta

import jpholiday

JST = timezone(timedelta(hours=9))
DATA_FILE = "data/stock_data.json"

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


def scrape_kabudragon(session: requests.Session, kind: str, date_str: str | None = None) -> list[dict]:
    """
    kind='stopdaka' → ストップ高
    kind='stopdana' → ストップ安
    date_str → 'YYYY-MM-DD' で過去日取得（省略時は最新）

    kabudragon テーブル構造 (class="rankingFrame"):
    td[0] 順位  td[1] コード  td[2] 銘柄名(a)  td[3] 市場
    td[4] 取引日  td[5] 取引値  td[6] 前日比  td[7] 変動率%
    td[8] 出来高  td[9] 高値  td[10] 安値
    """
    if date_str:
        y, m, d = date_str.split("-")
        url = f"https://www.kabudragon.com/ranking/{y}/{m}/{d}/{kind}.html"
    else:
        url = f"https://www.kabudragon.com/ranking/{kind}.html"

    print(f"  Fetching {url}")
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    # kabudragon は Shift-JIS
    resp.encoding = "shift_jis"

    soup = BeautifulSoup(resp.text, "html.parser")

    # class="rankingFrame" のテーブルを探す
    frame = soup.find("table", class_="rankingFrame")
    if not frame:
        print("  警告: rankingFrame が見つかりません")
        return []

    stocks = []
    for row in frame.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 8:
            continue
        # td[1] がコード（数字のみ）
        code = tds[1].get_text(strip=True)
        if not code.isdigit() and not (len(code) == 4 and code[:3].isdigit()):
            continue

        name_a = tds[2].find("a")
        name = name_a.get_text(strip=True) if name_a else tds[2].get_text(strip=True)
        market = tds[3].get_text(strip=True)
        price  = tds[5].get_text(strip=True).replace(",", "")
        change = tds[6].get_text(strip=True).replace(",", "")
        rate   = tds[7].get_text(strip=True).replace("%", "").strip().lstrip("+")

        stocks.append({
            "code":   code,
            "name":   name,
            "market": market,
            "price":  price,
            "change": change,
            "rate":   rate,
            "per":    "",
            "pbr":    "",
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

    # TARGET_DATE 環境変数で過去日バックフィル対応（YYYY-MM-DD）
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
    fetch_date = date_str if target else None

    try:
        print("ストップ高 取得中...")
        stop_high = scrape_kabudragon(session, "stopdaka", fetch_date)
        print(f"  → {len(stop_high)} 銘柄")

        time.sleep(2)

        print("ストップ安 取得中...")
        stop_low = scrape_kabudragon(session, "stopdana", fetch_date)
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
