from __future__ import annotations

import os
import re
import urllib.request
from datetime import date, timedelta


def _number(text: str, element_id: str) -> float:
    match = re.search(rf'id=["\']{re.escape(element_id)}["\'][^>]*>\s*([+-]?[0-9,.]+)', text, re.I)
    return float(match.group(1).replace(",", "")) if match else 0.0


def _score(per: float, pbr: float, dividend_yield: float, eps: float = 0, bps: float = 0) -> float:
    score = 0.0
    if 0 < per <= 25:
        score += 2.0
    if 0 < pbr <= 3:
        score += 1.0
    if dividend_yield > 0:
        score += 1.0
    if eps > 0 and bps > 0:
        score += 1.0
    return score


def naver_snapshot(ticker: str) -> dict[str, float | str]:
    request = urllib.request.Request(f"https://finance.naver.com/item/main.naver?code={ticker}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode("euc-kr", errors="ignore")
    per, pbr, dividend_yield = _number(text, "_per"), _number(text, "_pbr"), _number(text, "_dvr")
    if not any((per, pbr, dividend_yield)):
        return {"financial_score": 0.0, "financial_notes": "naver fundamentals unavailable"}
    return {"per": per, "pbr": pbr, "dividend_yield": dividend_yield, "financial_score": _score(per, pbr, dividend_yield), "financial_notes": "naver finance fundamentals"}


def pykrx_snapshot(ticker: str, end_day: date) -> dict[str, float | str]:
    from pykrx import stock

    start_day = end_day - timedelta(days=14)
    frame = stock.get_market_fundamental_by_date(start_day.strftime("%Y%m%d"), end_day.strftime("%Y%m%d"), ticker)
    if frame.empty:
        return {"financial_score": 0.0, "financial_notes": "pykrx fundamentals unavailable"}
    latest = frame.iloc[-1]
    per, pbr = float(latest.get("PER", 0) or 0), float(latest.get("PBR", 0) or 0)
    dividend_yield, eps, bps = float(latest.get("DIV", 0) or 0), float(latest.get("EPS", 0) or 0), float(latest.get("BPS", 0) or 0)
    return {"per": per, "pbr": pbr, "dividend_yield": dividend_yield, "eps": eps, "bps": bps, "financial_score": _score(per, pbr, dividend_yield, eps, bps), "financial_notes": "pykrx daily fundamentals"}


def snapshot(ticker: str, end_day: date | None = None) -> dict[str, float | str]:
    if os.environ.get("FUNDAMENTAL_LOOKUP", "0") != "1":
        return {"financial_score": 0.0, "financial_notes": "disabled"}
    errors = []
    try:
        result = naver_snapshot(ticker)
        if result.get("financial_score") or any(result.get(key) for key in ("per", "pbr", "dividend_yield")):
            return result
    except Exception as error:
        errors.append(f"naver:{type(error).__name__}")
    try:
        return pykrx_snapshot(ticker, end_day or date.today())
    except Exception as error:
        errors.append(f"pykrx:{type(error).__name__}")
    return {"financial_score": 0.0, "financial_notes": "lookup failed " + ",".join(errors)}
