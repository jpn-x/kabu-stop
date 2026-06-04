"""
既存 gap_data.csv に next_high / next_low カラムをバックフィルするスクリプト
"""
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path("data")
GAP_CSV = DATA_DIR / "gap_data.csv"

CSV_COLUMNS = [
    "stop_date", "next_date", "code", "name", "market", "stop_type",
    "prev_close", "next_open", "gap_yen", "gap_pct",
    "next_high", "next_low",
    "next_close", "range_yen", "range_pct", "volume", "yorazu", "reason",
]


def fetch_highlow(code: str, target_date: date):
    ticker_str = f"{code}.T"
    start = target_date.strftime("%Y-%m-%d")
    end = (target_date + timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        ticker = yf.Ticker(ticker_str)
        hist = ticker.history(start=start, end=end, auto_adjust=True)
        if hist.empty:
            return None, None
        target_str = target_date.strftime("%Y-%m-%d")
        for idx, row in hist.iterrows():
            row_date = idx.date() if hasattr(idx, "date") else idx
            if str(row_date) == target_str:
                high = round(float(row["High"]), 1)
                low  = round(float(row["Low"]),  1)
                return high, low
        return None, None
    except Exception as e:
        print(f"    ! {code} 失敗: {e}")
        return None, None


def main():
    df = pd.read_csv(GAP_CSV, encoding="utf-8-sig", dtype=str)
    print(f"読込: {len(df)} 行")

    # カラム追加
    if "next_high" not in df.columns:
        df["next_high"] = ""
    if "next_low" not in df.columns:
        df["next_low"] = ""

    # next_open があって next_high が空の行を対象に
    mask = (
        df["next_open"].fillna("") != ""
    ) & (
        df["next_high"].fillna("") == ""
    )
    targets = df[mask]
    print(f"バックフィル対象: {len(targets)} 行")

    for idx in targets.index:
        code      = df.at[idx, "code"]
        next_date = date.fromisoformat(df.at[idx, "next_date"])
        print(f"  {code} {next_date} ...", end=" ", flush=True)

        high, low = fetch_highlow(code, next_date)
        if high is not None:
            df.at[idx, "next_high"] = str(high)
            df.at[idx, "next_low"]  = str(low)
            print(f"高={high} 安={low}")
        else:
            print("データなし")
        time.sleep(0.3)

    # 列順を正規化して保存
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[CSV_COLUMNS]
    df.to_csv(GAP_CSV, index=False, encoding="utf-8-sig")

    filled = (df["next_high"].fillna("") != "").sum()
    print(f"\n完了: {filled}/{len(df)} 行に高値・安値あり → {GAP_CSV} 保存")


if __name__ == "__main__":
    main()
