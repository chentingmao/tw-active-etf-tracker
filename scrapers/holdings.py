"""ETF holdings scrapers for investment trust pages."""

from __future__ import annotations

import html
import json
import re
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from http_client import HttpClientError, fetch_bytes, fetch_text, post_json
from scrapers.etfinfo import fetch_etfinfo_payload


CAPITAL_FUND_IDS = {
    "00919": 195,
    "00982A": 399,
    "00992A": 500,
}

UNITRUST_FUND_CODES = {
    "00981A": "49YTW",
}

FUHWA_FUNDS = {
    "00991A": {
        "detail_url": "https://www.fhtrust.com.tw/ETF/etf_detail/ETF23#nav",
        "referer": "https://www.fhtrust.com.tw/ETF/etf_detail/ETF23",
        "excel_code": "ETF23",
    },
    "00998A": {
        "detail_url": "https://www.fhtrust.com.tw/ETF/etf_detail/ETF24#nav",
        "referer": "https://www.fhtrust.com.tw/ETF/etf_detail/ETF24",
        "excel_code": "ETF24",
    },
}

ETFINFO_FALLBACK_TICKERS = [
    "0050",
    "0056",
    "00878",
    "006208",
    "00980A",
    "00983A",
    "00984A",
    "00985A",
    "00986A",
    "00987A",
    "00990A",
    "00993A",
    "00994A",
    "00995A",
    "00996A",
    "00400A",
    "00401A",
    "00403A",
    "00404A",
    "00405A",
]

HOLDING_TICKERS = (
    list(CAPITAL_FUND_IDS) +
    list(UNITRUST_FUND_CODES) +
    list(FUHWA_FUNDS) +
    ETFINFO_FALLBACK_TICKERS
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(_to_float(value, float(default)))


def _date_only(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if compact:
        year, month, day = compact.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not match:
        return ""
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def fetch_capital_holdings(ticker: str) -> list[dict[str, Any]]:
    """Fetch holdings from Capital Investment Trust's JSON buyback API."""

    fund_id = CAPITAL_FUND_IDS.get(ticker)
    if not fund_id:
        return []

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.capitalfund.com.tw",
        "Referer": f"https://www.capitalfund.com.tw/etf/product/detail/{fund_id}/portfolio",
    }
    response = post_json(
        "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback",
        {"fundId": fund_id},
        headers=headers,
        timeout=20,
    )
    payload = response.data
    if not isinstance(payload, dict) or payload.get("code") not in (200, "200"):
        return []

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    data_date = _date_only((data.get("pcf") or {}).get("date2"))

    result = []
    for item in data.get("stocks") or []:
        ticker_code = _clean_text(item.get("stocNo"))
        if not re.match(r"^\d{4,5}[A-Z]?$", ticker_code):
            continue
        result.append({
            "ticker": ticker_code,
            "name": _clean_text(item.get("stocName")),
            "weight": _to_float(item.get("weight", item.get("weightRound"))),
            "shares": _to_int(item.get("share", item.get("shareFormat"))),
            "date": data_date,
            "source": "capitalfund",
        })
    return result


def fetch_unitrust_holdings(ticker: str) -> list[dict[str, Any]]:
    """Fetch holdings from Uni-President Investment Trust's ETF page."""

    fund_code = UNITRUST_FUND_CODES.get(ticker)
    if not fund_code:
        return []

    url = f"https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode={fund_code}"
    text = fetch_text(url, headers=_BROWSER_HEADERS, timeout=30, use_cookies=True).text
    match = re.search(r'<div\s+id="DataAsset"\s+data-content="([^"]*)"', text)
    if not match:
        return []

    assets = json.loads(html.unescape(match.group(1)))
    result = []
    for asset in assets:
        if asset.get("AssetCode") != "ST":
            continue
        for item in asset.get("Details") or []:
            ticker_code = _clean_text(item.get("DetailCode"))
            if not re.match(r"^\d{4,5}[A-Z]?$", ticker_code):
                continue
            result.append({
                "ticker": ticker_code,
                "name": _clean_text(item.get("DetailName")),
                "weight": _to_float(item.get("NavRate")),
                "shares": _to_int(item.get("Share")),
                "date": _date_only(item.get("TranDate")),
                "source": "unitrust",
            })
    return result


def fetch_fuhwa_holdings(ticker: str) -> list[dict[str, Any]]:
    """Fetch holdings from Fuh Hwa's official Excel export."""

    fund = FUHWA_FUNDS.get(ticker)
    if not fund:
        return []

    detail_url = fund["detail_url"]
    text = fetch_text(detail_url, headers=_BROWSER_HEADERS, timeout=20).text
    excel_code = fund["excel_code"]
    pattern = rf'href="(?P<path>/api/assetsExcel/{re.escape(excel_code)}/(?P<date>\d{{8}}))"'
    match = re.search(pattern, text)
    if not match:
        return _parse_fuhwa_html_table(text)

    export_url = "https://www.fhtrust.com.tw" + match.group("path")
    xlsx = fetch_bytes(
        export_url,
        headers={"User-Agent": _BROWSER_HEADERS["User-Agent"], "Referer": fund["referer"]},
        timeout=20,
    ).data
    rows = _read_xlsx_first_sheet(xlsx)
    return _parse_fuhwa_rows(rows, data_date=_date_only(match.group("date")))


def fetch_holdings_for_ticker(ticker: str) -> list[dict[str, Any]]:
    fetchers = []
    if ticker in CAPITAL_FUND_IDS:
        fetchers.append(fetch_capital_holdings)
    if ticker in UNITRUST_FUND_CODES:
        fetchers.append(fetch_unitrust_holdings)
    if ticker in FUHWA_FUNDS:
        fetchers.append(fetch_fuhwa_holdings)
    fetchers.append(fetch_etfinfo_holdings)

    for fetcher in fetchers:
        try:
            holdings = fetcher(ticker)
        except (HttpClientError, ValueError, json.JSONDecodeError, KeyError, TypeError, OSError):
            holdings = []
        if holdings:
            return holdings
    return []


def fetch_etfinfo_holdings(ticker: str) -> list[dict[str, Any]]:
    """Fallback parser for ETFInfo holding pages.

    This keeps new active ETFs visible even before their issuer-specific API is
    wired. Taiwan tickers such as ``2330 TT`` are normalized to ``2330`` while
    foreign exchange-qualified symbols such as ``AMZN US`` are preserved.
    """

    url = f"https://www.etfinfo.tw/etf/{ticker}/holdings"
    text = fetch_text(url, headers=_BROWSER_HEADERS, timeout=30).text
    nuxt_holdings = _parse_etfinfo_nuxt_holdings(text, ticker)
    if nuxt_holdings:
        return nuxt_holdings

    payload_holdings = _parse_etfinfo_payload_holdings(ticker)
    if payload_holdings:
        return payload_holdings

    return _parse_etfinfo_html_holdings(text)


def _parse_etfinfo_nuxt_holdings(text: str, ticker: str) -> list[dict[str, Any]]:
    match = re.search(
        r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>',
        text,
        re.S,
    )
    if not match:
        return []

    try:
        payload = json.loads(html.unescape(match.group(1)))
        root = _hydrate_nuxt_payload(payload)
    except (TypeError, ValueError, json.JSONDecodeError, KeyError, IndexError):
        return []

    return _parse_etfinfo_root_holdings(root, ticker)


def _parse_etfinfo_payload_holdings(ticker: str) -> list[dict[str, Any]]:
    try:
        root = fetch_etfinfo_payload(ticker, "holdings")
    except HttpClientError:
        return []
    return _parse_etfinfo_root_holdings(root, ticker)


def _parse_etfinfo_root_holdings(root: Any, ticker: str) -> list[dict[str, Any]]:
    data = root.get("data") if isinstance(root, dict) else {}
    base = data.get(f"etf-detail-base-{ticker}") if isinstance(data, dict) else {}
    holdings_payload = base.get("holdings") if isinstance(base, dict) else {}
    if not isinstance(holdings_payload, dict):
        return []

    snapshot_date = _date_only(holdings_payload.get("snapshotDate"))
    raw_items = holdings_payload.get("stocks")
    if not isinstance(raw_items, list) or not raw_items:
        raw_items = holdings_payload.get("holdings") or []

    result = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        ticker_code = _security_code(item.get("code"))
        if not _is_security_code(ticker_code):
            continue
        result.append({
            "ticker": ticker_code,
            "name": _clean_text(item.get("name")),
            "weight": _to_float(item.get("weight")),
            "shares": _to_int(item.get("shares")),
            "date": snapshot_date,
            "source": "etfinfo",
        })
    return result


def _hydrate_nuxt_payload(payload: list[Any]) -> Any:
    def hydrate(index: Any, memo: dict[int, Any]) -> Any:
        if not isinstance(index, int):
            return index
        if index in memo:
            return memo[index]

        value = payload[index]
        if isinstance(value, dict):
            obj: dict[str, Any] = {}
            memo[index] = obj
            for key, child in value.items():
                obj[key] = hydrate(child, memo)
            return obj

        if isinstance(value, list):
            if value and value[0] in {"Reactive", "ShallowReactive", "Ref", "ShallowRef"} and len(value) >= 2:
                return hydrate(value[1], memo)
            if value and value[0] == "Date" and len(value) >= 2:
                return hydrate(value[1], memo)
            arr: list[Any] = []
            memo[index] = arr
            arr.extend(hydrate(child, memo) for child in value)
            return arr

        return value

    return hydrate(0, {})


def _parse_etfinfo_html_holdings(text: str) -> list[dict[str, Any]]:
    date = ""
    date_match = re.search(r"持股快照[:：]\s*(\d{4}-\d{2}-\d{2})", text)
    if date_match:
        date = date_match.group(1)

    result = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        cells = []
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S):
            text_value = re.sub(r"<[^>]+>", " ", cell)
            cells.append(html.unescape(re.sub(r"\s+", " ", text_value)).strip())
        if len(cells) < 4:
            continue
        ticker_code = _security_code(cells[0])
        if not _is_security_code(ticker_code):
            continue
        result.append({
            "ticker": ticker_code,
            "name": cells[1],
            "weight": _to_float(cells[2]),
            "shares": _to_int(cells[3]),
            "date": date,
            "source": "etfinfo",
        })
    return result


def _security_code(value: Any) -> str:
    text = re.sub(r"\s+", " ", _clean_text(value).upper())
    match = re.fullmatch(r"(\d{4,5}[A-Z]?)\s+TT", text)
    if match:
        return match.group(1)

    match = re.fullmatch(r"TW000(\d{4})\d{3}", text)
    if match:
        return match.group(1)

    match = re.fullmatch(r"TW000(\d{5}[A-Z0-9])[A-Z0-9]", text)
    if match:
        return match.group(1)

    return text


def _is_security_code(value: Any) -> bool:
    text = _clean_text(value)
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{0,15}(?:\s+[A-Z]{1,4})?", text))


def _read_xlsx_first_sheet(content: bytes) -> list[list[str]]:
    with ZipFile(BytesIO(content)) as zf:
        shared_strings = _read_shared_strings(zf)
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in sheet.findall(".//x:sheetData/x:row", ns):
        cells: dict[int, str] = {}
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            col = _column_index(ref)
            if col < 0:
                continue
            value = _cell_value(cell, shared_strings, ns)
            cells[col] = value
        if cells:
            max_col = max(cells)
            rows.append([cells.get(i, "") for i in range(max_col + 1)])
    return rows


def _read_shared_strings(zf: ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings = []
    for item in root.findall("x:si", ns):
        parts = [node.text or "" for node in item.findall(".//x:t", ns)]
        strings.append("".join(parts))
    return strings


def _column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        return -1
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def _cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    if cell.attrib.get("t") == "s":
        value_node = cell.find("x:v", ns)
        if value_node is None or value_node.text is None:
            return ""
        index = int(value_node.text)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""

    if cell.attrib.get("t") == "inlineStr":
        parts = [node.text or "" for node in cell.findall(".//x:t", ns)]
        return "".join(parts)

    value_node = cell.find("x:v", ns)
    return value_node.text if value_node is not None and value_node.text is not None else ""


def _parse_fuhwa_rows(rows: list[list[str]], data_date: str = "") -> list[dict[str, Any]]:
    header_index = -1
    for index, row in enumerate(rows):
        normalized = [_clean_text(cell) for cell in row]
        if ("證券代號" in normalized or "證券代碼" in normalized) and "權重(%)" in normalized:
            header_index = index
            break

    if header_index < 0:
        return []

    result = []
    for row in rows[header_index + 1:]:
        if len(row) < 5:
            continue
        raw_code = _clean_text(row[0])
        if "代號" in raw_code or "代碼" in raw_code:
            break
        ticker_code = _security_code(raw_code)
        if not _is_security_code(ticker_code):
            continue
        result.append({
            "ticker": ticker_code,
            "name": _clean_text(row[1]),
            "weight": _to_float(row[4]),
            "shares": _to_int(row[2]),
            "date": data_date,
            "source": "fuhwa",
        })
    return result


def _parse_fuhwa_html_table(text: str) -> list[dict[str, Any]]:
    marker = text.find("2330")
    start = text.rfind('<tbody class="fundTable-content">', 0, marker)
    if marker < 0 or start < 0:
        return []
    end = text.find("</tbody>", start)
    if end < 0:
        return []

    block = text[start:end]
    result = []
    for row in re.findall(r"<tr>(.*?)</tr>", block, re.S):
        cells = []
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S):
            text_value = re.sub(r"<[^>]+>", " ", cell)
            cells.append(html.unescape(re.sub(r"\s+", " ", text_value)).strip())
        if len(cells) < 5:
            continue
        ticker_code = _security_code(cells[0])
        if not _is_security_code(ticker_code):
            continue
        result.append({
            "ticker": ticker_code,
            "name": cells[1],
            "weight": _to_float(cells[4]),
            "shares": _to_int(cells[2]),
            "date": "",
            "source": "fuhwa-html",
        })
    return result
