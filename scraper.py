"""
株探 ストップ高・ストップ安 スクレイパー
毎日 16:30 JST（引け後）に実行する。

株探は GitHub Actions の IP をブロックするため、
Cloudflare Worker のプロキシ経由で取得する:
  https://stop-data.jp-x.workers.dev/proxy?url=<kabutan URL>

  mode=3_1 → ストップ高
  mode=3_2 → ストップ安
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
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
    "https://stop-data.jp-x.workers.dev/proxy",
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


PAGE_DATE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")


def parse_page_date(soup: BeautifulSoup) -> str | None:
    """株探ページが示すデータの取引日（YYYY-MM-DD）を返す。取れなければ None。

    <div class="meigara_count"><ul><li>2026年10月09日</li><li>16:00現在</li>...
    GitHub Actions の cron は数時間遅れることがあり、実行時刻(now)で日付ラベルを付けると
    前日のデータが翌日の日付で保存されてしまう。そのためページ側の日付を正とする。
    """
    box = soup.find(class_="meigara_count")
    if not box:
        return None
    m = PAGE_DATE_RE.search(box.get_text(" ", strip=True))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def scrape_kabutan(session: requests.Session, mode: str) -> tuple[list[dict], str | None]:
    """
    mode='3_1' → ストップ高
    mode='3_2' → ストップ安

    戻り値: (銘柄リスト, ページが示す取引日 YYYY-MM-DD または None)

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
    page_date = parse_page_date(soup)
    table = soup.find("table", class_="stock_table")
    if not table:
        print("  警告: stock_table が見つかりません")
        return [], page_date

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

    return stocks, page_date


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
    session = make_session()

    try:
        print("ストップ高 取得中...")
        stop_high, date_high = scrape_kabutan(session, "3_1")
        print(f"  → {len(stop_high)} 銘柄 (ページの取引日: {date_high})")

        time.sleep(2)

        print("ストップ安 取得中...")
        stop_low, date_low = scrape_kabutan(session, "3_2")
        print(f"  → {len(stop_low)} 銘柄 (ページの取引日: {date_low})")

    except requests.RequestException as e:
        print(f"エラー: スクレイピング失敗 - {e}", file=sys.stderr)
        sys.exit(1)

    # 取引日（日付ラベル）はページが示す日付を正とする。実行時刻(now)は使わない。
    # cron が数時間遅れて日付をまたぐと、前日データが翌日ラベルで保存されるのを防ぐため。
    # 日付が特定できない/高安で食い違う場合は、誤ラベルで保存するより中止して次回に任せる。
    if not date_high or date_high != date_low:
        print(f"エラー: 取引日を特定できません（高={date_high} / 安={date_low}）。"
              "誤った日付での保存を避けるため中止します", file=sys.stderr)
        sys.exit(1)

    date_str  = date_high
    today     = date.fromisoformat(date_str)
    month_key = date_str[:7]

    # TARGET_DATE は「この日のデータのはず」という確認用（株探はリアルタイム板で過去日は取得不可）。
    # ページの取引日と違えば、誤ラベルを避けるため保存しない。
    target = os.environ.get("TARGET_DATE", "").strip()
    if target and target != date_str:
        print(f"エラー: TARGET_DATE={target} だが、ページの取引日は {date_str}。保存しません",
              file=sys.stderr)
        sys.exit(1)

    print(f"=== 株データ取得: {date_str} (実行 {now:%Y-%m-%d %H:%M} JST) ===")

    if today.weekday() >= 5 or jpholiday.is_holiday(today):
        print(f"  {date_str} は非営業日のためスキップ")
        sys.exit(0)

    if today == now.date() and now.hour * 60 + now.minute < 15 * 60 + 30:
        print("  注意: 引け前の実行のため、取得データは途中経過です（引け後の実行で上書きされます）")

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
