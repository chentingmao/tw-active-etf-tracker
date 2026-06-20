"""
使用 FinMind 更新 ETF 價格，並從投信官方頁面/API 抓取 ETF 持股

執行：
  python fetch_finmind.py               # 僅更新 ETF 價格（快，無需 Selenium）
  python fetch_finmind.py --holdings    # 同時抓取投信官方 ETF 持股
  python fetch_finmind.py --token YOUR  # 使用 FinMind token（提高速率上限）

FinMind v4 能提供的 ETF 資料：
  ✓ 每日收盤價（TaiwanStockPrice）—含所有主動型 ETF（00981A, 00982A 等）
  ✗ ETF 成分股—FinMind v4 無此資料集；持股需透過投信公司頁面/API/Excel
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

ETF_CONFIG = {
    "0050":   {"name": "元大台灣50",       "color": "#4a9eff", "tags": ["市值型"],              "type": "passive"},
    "0056":   {"name": "元大高股息",        "color": "#4adfb8", "tags": ["高股息型"],            "type": "passive"},
    "00878":  {"name": "國泰永續高股息",    "color": "#a78bfa", "tags": ["高股息/ESG"],          "type": "passive"},
    "00919":  {"name": "群益台灣精選高息",  "color": "#f472b6", "tags": ["高股息型", "持股待補"], "type": "passive"},
    "006208": {"name": "富邦台50",          "color": "#60a5fa", "tags": ["市值型", "同0050"],    "type": "passive"},
    "009816": {"name": "凱基台灣TOP50",    "color": "#38bdf8", "tags": ["市值型", "TOP50"], "type": "passive"},
    "00850": {"name": "元大台灣ESG永續",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00913": {"name": "兆豐台灣晶圓製造",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00927": {"name": "群益半導體收益",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00947": {"name": "台新台灣IC設計",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00928": {"name": "中信上櫃ESG30",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00935": {"name": "野村台灣新科技50",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00888": {"name": "永豐台灣ESG",   "color": "#818cf8", "tags": ["市值型"], "type": "passive"},
    "00980A": {"name": "主動野村臺灣優選",  "color": "#2563eb", "tags": ["主動", "成長型"],      "type": "active"},
    "00981A": {"name": "主動統一台股增長",  "color": "#34d399", "tags": ["台股型", "主動"],      "type": "active"},
    "00982A": {"name": "主動群益台灣強棒",  "color": "#f87171", "tags": ["主動"],                "type": "active"},
    "00983A": {"name": "主動中信ARK創新",   "color": "#0ea5e9", "tags": ["主動", "創新"],        "type": "active"},
    "00984A": {"name": "主動安聯台灣高息",  "color": "#16a34a", "tags": ["主動", "高股息型"],    "type": "active"},
    "00985A": {"name": "主動野村台灣50",    "color": "#1d4ed8", "tags": ["主動", "市值型"],      "type": "active"},
    "00986A": {"name": "主動台新龍頭成長",  "color": "#db2777", "tags": ["主動", "成長型"],      "type": "active"},
    "00987A": {"name": "主動台新優勢成長",  "color": "#be123c", "tags": ["主動", "成長型"],      "type": "active"},
    "00990A": {"name": "主動元大AI新經濟",  "color": "#7c3aed", "tags": ["主動", "AI"],          "type": "active"},
    "00992A": {"name": "主動群益科技創新",  "color": "#818cf8", "tags": ["科技型", "主動"],      "type": "active"},
    "00991A": {"name": "主動復華未來50",    "color": "#facc15", "tags": ["主動"],                "type": "active"},
    "00993A": {"name": "主動安聯台灣",      "color": "#15803d", "tags": ["主動", "成長型"],      "type": "active"},
    "00994A": {"name": "主動第一金台股優",  "color": "#0891b2", "tags": ["主動", "多因子"],      "type": "active"},
    "00995A": {"name": "主動中信台灣卓越",  "color": "#0284c7", "tags": ["主動", "成長型"],      "type": "active"},
    "00996A": {"name": "主動兆豐台灣豐收",  "color": "#65a30d", "tags": ["主動", "成長型"],      "type": "active"},
    "00998A": {"name": "主動復華金融股息",  "color": "#ca8a04", "tags": ["主動", "股息型"],      "type": "active"},
    "00400A": {"name": "主動國泰動能高息",  "color": "#0d9488", "tags": ["主動", "高股息型"],    "type": "active"},
    "00401A": {"name": "主動摩根台灣鑫收",  "color": "#9333ea", "tags": ["主動", "高股息型"],    "type": "active"},
    "00403A": {"name": "主動統一升級50",    "color": "#14b8a6", "tags": ["主動", "市值型"],      "type": "active"},
    "00404A": {"name": "主動聯博動能50",    "color": "#6366f1", "tags": ["主動", "收益型"],      "type": "active"},
    "00405A": {"name": "主動富邦台灣龍耀",  "color": "#84cc16", "tags": ["主動", "成長型"],      "type": "active"},
}

ACTIVE_TICKERS = [t for t, c in ETF_CONFIG.items() if c["type"] == "active"]
ALL_TICKERS = list(ETF_CONFIG.keys())


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── 投信 ETF 持股抓取 ────────────────────────────────────────────────────

from scrapers.holdings import HOLDING_TICKERS, fetch_holdings_for_ticker


def fetch_active_holdings_direct() -> dict[str, list[dict]]:
    """從投信官方頁面/API 取得 ETF 持股（不需要 Selenium）"""
    result: dict[str, list[dict]] = {}
    for ticker in HOLDING_TICKERS:
        holdings = fetch_holdings_for_ticker(ticker)
        if holdings:
            result[ticker] = holdings
            source = holdings[0].get("source", "crawler")
            date = holdings[0].get("date", "")
            suffix = f", date={date}" if date else ""
            print(f"  [OK] {ticker}: {source} {len(holdings)} holdings{suffix}")
        else:
            print(f"  [--] {ticker}: holding crawler failed")
        time.sleep(0.3)
    return result


# ─── 資料更新 ─────────────────────────────────────────────────────────────

def sync_etf_cards():
    """確保 etf_cards.json 包含 ETF_CONFIG 裡的所有標的。"""
    path = DATA_DIR / "etf_cards.json"
    existing_cards = load_json(path) if path.exists() else []
    existing = {card["ticker"]: card for card in existing_cards}
    cards = []

    for ticker, cfg in ETF_CONFIG.items():
        card = dict(existing.get(ticker, {}))
        card["ticker"] = ticker
        card["name"] = cfg["name"]
        card["color"] = cfg["color"]
        card["tags"] = cfg["tags"]
        card["type"] = cfg["type"]
        card.setdefault("price", 0)
        card.setdefault("price_date", "")
        card.setdefault("date", "")
        card.setdefault("aum", "0億")
        cards.append(card)

    save_json(path, cards)
    print(f"  etf_cards.json 已同步 {len(cards)} 檔 ETF")

def update_etf_cards_prices(prices: dict[str, dict]):
    """用 FinMind 價格更新 etf_cards.json"""
    path = DATA_DIR / "etf_cards.json"
    if not path.exists():
        return
    cards = load_json(path)
    updated = 0
    for card in cards:
        ticker = card["ticker"]
        if ticker in prices:
            p = prices[ticker]
            card["price"] = round(p["close"], 2)
            card["price_date"] = p.get("date", "")
            # 保留已有的 date，不要用股價日期覆蓋（持股日期才是重要的）
            updated += 1
    save_json(path, cards)
    print(f"  etf_cards.json 已更新 {updated} 筆 ETF 價格")


def update_etf_cards_holdings_dates(holdings_by_etf: dict[str, list[dict]]):
    """用持股來源日期更新 etf_cards.json 的 date 欄位。"""
    path = DATA_DIR / "etf_cards.json"
    if not path.exists():
        return

    cards = load_json(path)
    updated = 0
    for card in cards:
        ticker = card["ticker"]
        holdings = holdings_by_etf.get(ticker) or []
        dates = sorted({h.get("date", "") for h in holdings if h.get("date")})
        if dates:
            card["date"] = dates[-1]
            updated += 1
    save_json(path, cards)
    print(f"  etf_cards.json 已更新 {updated} 筆 ETF 持股日期")


def rebuild_cross_data(all_holdings: dict[str, list[dict]]):
    """根據最新持股重建 cross_data.json"""
    etf_aums = _load_etf_aums()
    stock_map: dict[str, dict] = {}

    for etf_ticker, holdings in all_holdings.items():
        cfg = ETF_CONFIG.get(etf_ticker, {})
        etf_color = cfg.get("color", "#888888")
        aum = etf_aums.get(etf_ticker, 0)

        for h in holdings:
            stk = h["ticker"]
            if not stk:
                continue
            if stk not in stock_map:
                stock_map[stk] = {"name": h.get("name", ""), "etfs": []}
            capital_yi = round(aum * h["weight"] / 100, 1) if aum and h.get("weight") else 0
            stock_map[stk]["etfs"].append({
                "etf_ticker": etf_ticker,
                "etf_name": cfg.get("name", etf_ticker),
                "color": etf_color,
                "weight": round(float(h.get("weight", 0)), 4),
                "capital_yi": capital_yi,
                "shares": h.get("shares", 0),
                "date": h.get("date", ""),
            })

    result = []
    for stk, info in stock_map.items():
        etfs = info["etfs"]
        result.append({
            "ticker": stk,
            "name": info["name"],
            "etf_count": len(etfs),
            "max_weight": max((e["weight"] for e in etfs), default=0),
            "total_capital": round(sum(e["capital_yi"] for e in etfs), 1),
            "total_shares": sum(e["shares"] for e in etfs),
            "etfs": etfs,
        })

    result.sort(key=lambda x: -x["etf_count"])
    save_json(DATA_DIR / "cross_data.json", result)
    print(f"  cross_data.json 已更新：{len(result)} 筆股票")


def _load_etf_aums() -> dict[str, float]:
    path = DATA_DIR / "etf_cards.json"
    if not path.exists():
        return {}
    aums = {}
    for c in load_json(path):
        try:
            aum_str = c.get("aum", "")
            if "兆" in aum_str:
                val = float(aum_str.replace("兆", "").strip()) * 10000
            elif "億" in aum_str:
                val = float(aum_str.replace("億", "").strip())
            else:
                val = 0
            aums[c["ticker"]] = val
        except ValueError:
            aums[c["ticker"]] = 0
    return aums


def _load_existing_holdings() -> dict[str, list[dict]]:
    """從現有 cross_data.json 重建 holdings dict"""
    path = DATA_DIR / "cross_data.json"
    if not path.exists():
        return {}
    cross = load_json(path)
    holdings: dict[str, list[dict]] = {}
    for row in cross:
        for e in row["etfs"]:
            t = e["etf_ticker"]
            if t not in holdings:
                holdings[t] = []
            holdings[t].append({
                "ticker": row["ticker"],
                "name": row["name"],
                "weight": e["weight"],
                "shares": e["shares"],
                "date": e.get("date", ""),
            })
    return holdings


# ─── 主程式 ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FinMind ETF 資料更新")
    parser.add_argument("--token", default="", help="FinMind API token（可省略）")
    parser.add_argument("--holdings", action="store_true", help="同時抓取投信官方 ETF 持股")
    args = parser.parse_args()

    from scrapers.finmind import fetch_all_etf_prices

    print("=" * 60)
    print("FinMind ETF 資料更新")
    print(f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── 1. 用 FinMind 更新所有 ETF 收盤價 ──
    sync_etf_cards()
    print(f"\n[1/2] 抓取 ETF 最新收盤價（FinMind TaiwanStockPrice）")
    prices = fetch_all_etf_prices(ALL_TICKERS, token=args.token, delay_sec=0.5)
    update_etf_cards_prices(prices)
    try:
        from premium_discount import rebuild_etf_premium_discount
        print(f"\n[premium] 更新 ETF 溢/折價（ETFInfo）")
        premium_path = rebuild_etf_premium_discount(ETF_CONFIG)
        print(f"  溢/折價資料：{premium_path.name}")
    except Exception as e:
        print(f"  溢/折價資料更新失敗，略過：{e}")
    try:
        from performance_data import rebuild_active_etf_performance
        print(f"\n[performance] 更新主動式 ETF 績效")
        performance_path = rebuild_active_etf_performance(ETF_CONFIG, token=args.token)
        print(f"  績效資料：{performance_path.name}")
    except Exception as e:
        print(f"  績效資料更新失敗，略過：{e}")

    # ── 2. 持股抓取（選用）──
    if args.holdings:
        print(f"\n[2/2] 抓取投信官方 ETF 持股")
        new_holdings = fetch_active_holdings_direct()

        if new_holdings:
            # 更新前先儲存舊快照
            try:
                from update_history import save_snapshot, get_data_date
                save_snapshot(get_data_date())
            except Exception as e:
                print(f"  快照儲存失敗（非必要）: {e}")
            # 合併現有持股 + 新抓的
            existing = _load_existing_holdings()
            merged = {**existing, **new_holdings}  # 新資料覆蓋舊資料
            rebuild_cross_data(merged)
            update_etf_cards_holdings_dates(new_holdings)
            # 儲存新快照並重建歷史
            try:
                from update_history import (
                    rebuild_changes,
                    rebuild_history,
                    save_daily_report,
                    save_daily_holdings_snapshot,
                    save_snapshot,
                    get_data_date,
                )
                daily_path = save_daily_holdings_snapshot(get_data_date())
                changes_path = rebuild_changes(get_data_date())
                report_path = save_daily_report(get_data_date())
                save_snapshot(get_data_date())
                rebuild_history()
                print(f"  每日持股快照：{daily_path.name}")
                print(f"  持股變動檔：{changes_path.name}")
                print(f"  每日報表：{report_path.name}")
            except Exception as e:
                print(f"  歷史更新失敗（非必要）: {e}")
        else:
            print("  投信持股爬蟲無法取得任何持股，cross_data.json 保持不變")
    else:
        print(f"\n[2/2] 跳過持股更新（加 --holdings 參數可抓投信官方持股）")
        print("  → 可執行 python update_data.py 進行價格與持股更新")

    print(f"\n完成！")
    print(f"  價格更新：{len(prices)}/{len(ALL_TICKERS)} 檔")
    print("=" * 60)


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).parent)
    main()
