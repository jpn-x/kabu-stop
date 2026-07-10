"""
Kabutan ストップ高・ストップ安 スクレイパー
毎日 16:00 JST 以降に実行する
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

# Chrome 124 相当のフルヘッダーセット（Kabutan の bot 検出を回避）
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


def make_session() -> requests.Session:
    """セッションを作成し、トップページを訪問してCookieを取得する"""
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    try:
        # トップページを先に取得してCookieとセッションを確立
        s.headers.update({"Referer": ""})
        s.get("https://kabutan.jp/", timeout=15)
        time.sleep(1)
    except Exception:
        pass
    return s


def scrape_kabutan(session: requests.Session, mode: str, date_str: str | None = None) -> list[dict]:
    """
    mode='3_1' → ストップ高
    mode='3_2' → ストップ安

    株探のテーブル構造（table.stock_table）:
    <td class="tac"> コード          ← tds[0]
    <th class="tal"> 銘柄名          ← th要素（tdではない！）
    <td class="tac"> 市場            ← tds[1]
    <td class="gaiyou_icon"> 概要    ← tds[2] スキップ
    <td class="chart_icon">  チャート ← tds[3] スキップ
    <td>             株価            ← tds[4]
    <td>             Sフラグ         ← tds[5] スキップ
    <td class="w61"> 前日比          ← tds[6]
    <td class="w50"> 変動率%         ← tds[7]
    <td class="news_icon"> ニュース  ← tds[8] スキップ
    <td>             PER             ← tds[9]
    <td>             PBR             ← tds[10]
    <td>             利回り          ← tds[11]
    """
    url = f"https://kabutan.jp/warning/?mode={mode}"
    if date_str:
        url += f"&date={date_str}"
    print(f"  Fetching {url}")

    session.headers.update({"Referer": "https://kabutan.jp/"})
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")

    # メインテーブルを特定（class="stock_table"）
    table = soup.find("table", class_="stock_table")
    if not table:
        print("  警告: stock_table が見つかりません")
        return []

    tbody = table.find("tbody")
    if not tbody:
        return []

    stocks = []

    for row in tbody.find_all("tr"):
        tds = row.find_all("td")
        if len(tds) < 10:
            continue

        # コード（td[0]のリンクテキスト）
        code_link = tds[0].find("a")
        if not code_link:
            continue
        code = code_link.get_text(strip=True)
        if not code:
            continue

        # 銘柄名（th要素 class="tal"）
        name_th = row.find("th", class_="tal")
        name = name_th.get_text(strip=True) if name_th else ""

        # 市場（td[1] class="tac"）
        market = tds[1].get_text(strip=True)

        # 株価（td[4]）
        price = tds[4].get_text(strip=True).replace(",", "")

        # 前日比（td[6] class="w61"）
        change = tds[6].get_text(strip=True).replace(",", "")

        # 変動率（td[7] class="w50"、末尾の % を除去）
        rate = tds[7].get_text(strip=True).replace("%", "").strip().lstrip("+")

        # PER / PBR（td[9] / td[10]、「－」は空文字に）
        def clean_val(td):
            v = td.get_text(strip=True).replace(",", "")
            return "" if "－" in v or v == "-" else v

        per = clean_val(tds[9])
        pbr = clean_val(tds[10])

        stocks.append({
            "code":   code,
            "name":   name,
            "market": market,
            "price":  price,
            "change": change,
            "rate":   rate,
            "per":    per,
            "pbr":    pbr,
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
    # 旧フォーマット（list）から新フォーマット（dict）へ移行
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
        now_str = now.isoformat()
    else:
        today = now.date()
        date_str  = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")
        now_str = now.isoformat()

    print(f"=== 株データ取得: {date_str} ===")

    # 土日・祝日はスキップ（カブタンのデータは前営業日のものになるため）
    if today.weekday() >= 5 or jpholiday.is_holiday(today):
        print(f"  {date_str} は非営業日のためスキップ")
        sys.exit(0)

    session = make_session()

    try:
        print("ストップ高 取得中...")
        stop_high = scrape_kabutan(session, "3_1", date_str if target else None)
        print(f"  → {len(stop_high)} 銘柄")

        time.sleep(2)

        print("ストップ安 取得中...")
        stop_low = scrape_kabutan(session, "3_2", date_str if target else None)
        print(f"  → {len(stop_low)} 銘柄")

    except requests.RequestException as e:
        print(f"エラー: スクレイピング失敗 - {e}", file=sys.stderr)
        sys.exit(1)

    today_record = {
        "date":       date_str,
        "updated_at": now_str,
        "stop_high":  stop_high,
        "stop_low":   stop_low,
    }

    all_data = load_existing()

    # 当月リストがなければ初期化
    all_data.setdefault(month_key, [])

    # 同日データがあれば置き換え（再実行対応）
    all_data[month_key] = [d for d in all_data[month_key] if d.get("date") != date_str]
    all_data[month_key].append(today_record)

    # 月内を日付降順に整列
    all_data[month_key].sort(key=lambda x: x["date"], reverse=True)

    save(all_data)
    print(f"完了: {DATA_FILE} に保存しました ({month_key} に {len(all_data[month_key])} 日分)")


if __name__ == "__main__":
    main()

