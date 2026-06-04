"""
既存 gap_data.csv の reason カラムを過去分バックフィルするスクリプト
"""
import re
import time
from datetime import date, timedelta
from pathlib import Path

import jpholiday
import pandas as pd
import requests

DATA_DIR = Path("data")
GAP_CSV = DATA_DIR / "gap_data.csv"


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and not jpholiday.is_holiday(d)


def next_business_day(d: date) -> date:
    nd = d + timedelta(days=1)
    while not is_business_day(nd):
        nd += timedelta(days=1)
    return nd


def fetch_day_reasons(nbd: date, sample_codes: list) -> dict:
    """
    カブタン「前日に動いた銘柄 part2」から {code: reason} を取得。
    sample_codes を順番に試す。
    """
    for sample_code in sample_codes:
        try:
            list_url = f"https://kabutan.jp/stock/news/?code={sample_code}"
            resp = requests.get(
                list_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=10,
            )
            resp.raise_for_status()
            text = resp.content.decode("utf-8", errors="ignore")

            nbd_str = nbd.strftime("%Y-%m-%d")
            rows_html = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL)
            article_url = None
            for row in rows_html:
                if nbd_str in row and "前日に動いた銘柄" in row:
                    m = re.search(r'href="(/stock/news\?[^"]+&b=(n\d+))"', row)
                    if m:
                        b_id = m.group(2)
                        article_url = f"https://kabutan.jp/news/marketnews/?b={b_id}"
                        break

            if not article_url:
                continue

            print(f"    → {nbd_str} 記事取得: {article_url}")
            time.sleep(0.5)
            resp2 = requests.get(
                article_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=10,
            )
            resp2.raise_for_status()
            text2 = resp2.content.decode("utf-8", errors="ignore")

            entries = re.findall(
                r'href="/stock/\?code=(\w+)"[^>]*>[^<]+</a>&gt;[^<\n]*<br\s*/>\s*([^<\n。．]{4,})[。．]?<br',
                text2,
            )
            result = {}
            for code, reason in entries:
                clean = reason.strip()
                if clean:
                    result[code] = clean[:120]

            if result:
                print(f"    → 取得完了: {len(result)} 銘柄")
                return result
            else:
                print(f"    → {sample_code} では理由0件、次のコードを試す")

        except Exception as e:
            print(f"    ! {sample_code} 失敗: {e}")

    print(f"    → {nbd} の理由が取得できず")
    return {}


def main():
    df = pd.read_csv(GAP_CSV, dtype=str)
    print(f"読込: {len(df)} 行, カラム: {list(df.columns)}")

    # reason カラムがなければ追加
    if "reason" not in df.columns:
        df["reason"] = ""
    else:
        # 既存の reason が空白の行だけ対象にする
        pass

    # stop_date ごとにグループ化
    unique_dates = sorted(df["stop_date"].dropna().unique())
    print(f"対象日付: {len(unique_dates)} 日")

    for stop_date_str in unique_dates:
        stop_date = date.fromisoformat(stop_date_str)
        nbd = next_business_day(stop_date)

        # この stop_date の行で reason が空のものを対象に
        mask = (df["stop_date"] == stop_date_str) & (df["reason"].fillna("") == "")
        target_rows = df[mask]
        if target_rows.empty:
            print(f"\n{stop_date_str}: 全行に reason 済み → スキップ")
            continue

        codes = target_rows["code"].dropna().tolist()
        print(f"\n=== {stop_date_str} → nbd={nbd}  対象{len(codes)}銘柄 ===")

        # 最大5つのコードを試す
        reasons = fetch_day_reasons(nbd, codes[:5])

        if reasons:
            for idx in df[mask].index:
                code = df.at[idx, "code"]
                if code in reasons:
                    df.at[idx, "reason"] = reasons[code]
                    print(f"      {code}: {reasons[code][:60]}")

        time.sleep(1.0)  # 礼儀として1秒待機

    # 列順を正規化
    desired_columns = [
        "stop_date", "next_date", "code", "name", "market", "stop_type",
        "prev_close", "next_open", "gap_yen", "gap_pct",
        "next_close", "range_yen", "range_pct", "volume", "yorazu", "reason",
    ]
    for col in desired_columns:
        if col not in df.columns:
            df[col] = ""
    df = df[desired_columns]

    df.to_csv(GAP_CSV, index=False, encoding="utf-8-sig")
    filled = (df["reason"].fillna("") != "").sum()
    print(f"\n完了: {filled}/{len(df)} 行に reason あり → {GAP_CSV} 保存")


if __name__ == "__main__":
    main()
