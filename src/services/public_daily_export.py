"""Write the smallest public-safe daily artifact for Finsance.

The normal report contains private trading levels and raw provider payloads.
This module deliberately builds a new allow-list payload instead of serialising
AnalysisResult.to_dict().
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


SCHEMA_VERSION = "finsance-quanttrade-daily-v1"
DEFAULT_OUTPUT_DIR = Path("reports") / "finsance"
_SECRET_RE = re.compile(r"(?i)(?:bearer\s+|sk-|AIza)[A-Za-z0-9._-]{8,}")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")

_KNOWN_THAI_TEXT = {
    "看多": "ขาขึ้น",
    "强烈看多": "ขาขึ้นแรง",
    "看空": "ขาลง",
    "强烈看空": "ขาลงแรง",
    "震荡": "แกว่งตัว",
    "低": "ต่ำ",
    "中": "ปานกลาง",
    "高": "สูง",
    "技术面数据部分可用": "ข้อมูลทางเทคนิคมีเพียงบางส่วน",
    "technical: partial": "ข้อมูลเทคนิค: บางส่วน",
    "筹码分布数据缺失": "ข้อมูลโครงสร้างผู้ถือหุ้นยังไม่พร้อม",
    "筹码分布数据缺失，无法进行筹码结构分析": "ข้อมูลโครงสร้างผู้ถือหุ้นยังไม่พร้อม จึงยังวิเคราะห์โครงสร้างไม่ได้",
    "新闻舆情数据缺失": "ข้อมูลข่าวและ sentiment ยังไม่พร้อม",
    "近三日无相关新闻数据": "ไม่มีข่าวที่เกี่ยวข้องในช่วง 3 วันที่ผ่านมา",
    "非交易日，无实时盘中数据": "วันนี้ไม่ใช่วันซื้อขาย จึงไม่มีข้อมูลระหว่างวันที่แบบเรียลไทม์",
    "当前为非交易日，无法获取实时盘中数据": "วันนี้ไม่ใช่วันซื้อขาย จึงไม่สามารถรับข้อมูลระหว่างวันที่แบบเรียลไทม์ได้",
    "市场系统性风险，非交易日数据滞后风险，筹码分布数据缺失导致无法全面评估筹码健康状况。": "ความเสี่ยงระบบตลาด ข้อมูลวันหยุดอาจล่าช้า และข้อมูลโครงสร้างผู้ถือหุ้นยังไม่พร้อม จึงประเมินภาพรวมได้ไม่ครบ",
    "资金流数据缺失": "ไม่มีข้อมูลกระแสเงินทุน",
    "资金流数据不可用": "กระแสเงินทุนยังไม่พร้อมใช้งาน",
}


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _text(value: Any, limit: int = 600) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_text(value: Any, limit: int = 600) -> Optional[str]:
    text = _text(value, limit)
    return _SECRET_RE.sub("[redacted]", text) if text else None


def _items(value: Any, limit: int = 5) -> list[str]:
    if value is None:
        return []
    values: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        text = _safe_text(item, 500)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _thai_text(value: Any, fallback: Optional[str] = None) -> Optional[str]:
    text = _safe_text(value, 800)
    if not text:
        return fallback
    for source, target in _KNOWN_THAI_TEXT.items():
        text = text.replace(source, target)
    if _CJK_RE.search(text):
        return fallback
    return text if _THAI_RE.search(text) else fallback


def _thai_items(value: Any, *, fallback: Optional[str] = None) -> list[str]:
    translated: list[str] = []
    for item in _items(value):
        text = _thai_text(item)
        if text and text not in translated:
            translated.append(text)
    return translated or ([fallback] if fallback else [])


def _public_text(value: Any, *, thai_output: bool, limit: int = 800) -> Optional[str]:
    """Keep narrative fields public-safe without inventing translations."""
    if thai_output:
        return _thai_text(value)
    return _safe_text(value, limit)


def _overview_block(result: Any, key: str, *, thai_output: bool) -> dict[str, Any]:
    overview = _value(result, "analysis_context_pack_overview")
    blocks = overview.get("blocks") if isinstance(overview, Mapping) else None
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, Mapping) and block.get("key") == key:
                status = _safe_text(block.get("status"), 32)
                if thai_output and status and _CJK_RE.search(status):
                    status = "unavailable"
                source = _safe_text(block.get("source"), 120)
                if thai_output and source and _CJK_RE.search(source):
                    source = None
                return {
                    "status": status,
                    "source": source,
                    "warnings": (_thai_items(block.get("warnings")) if thai_output else _items(block.get("warnings"), limit=3)),
                    "missing_reasons": (_thai_items(block.get("missing_reasons")) if thai_output else _items(block.get("missing_reasons"), limit=3)),
                }
    return {"status": "unavailable", "source": None, "warnings": [], "missing_reasons": []}


def _safe_metric(value: Any, *, limit: int = 80) -> Any:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 6) if math.isfinite(number) else None
    text = _safe_text(value, limit)
    return text if text and not _CJK_RE.search(text) else None


def _safe_metric_map(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: metric
        for key in keys
        if (metric := _safe_metric(value.get(key))) is not None
    }


def _technical_payload(result: Any, *, thai_output: bool) -> dict[str, Any]:
    snapshot = _value(result, "market_snapshot")
    snapshot = _safe_metric_map(
        snapshot,
        ("date", "volume", "volume_ratio", "turnover_rate"),
    )
    return {
        **_overview_block(result, "technical", thai_output=thai_output),
        "summary": _public_text(_value(result, "technical_analysis"), thai_output=thai_output),
        "trend": _public_text(_value(result, "trend_analysis"), thai_output=thai_output),
        "moving_average": _public_text(_value(result, "ma_analysis"), thai_output=thai_output),
        "volume": _public_text(_value(result, "volume_analysis"), thai_output=thai_output),
        "pattern": _public_text(_value(result, "pattern_analysis"), thai_output=thai_output),
        "snapshot": snapshot,
    }


def _fundamentals_payload(result: Any, *, thai_output: bool) -> dict[str, Any]:
    context = _value(result, "fundamental_context")
    context = context if isinstance(context, Mapping) else {}
    valuation = context.get("valuation") if isinstance(context.get("valuation"), Mapping) else {}
    growth = context.get("growth") if isinstance(context.get("growth"), Mapping) else {}
    earnings = context.get("earnings") if isinstance(context.get("earnings"), Mapping) else {}
    valuation_data = valuation.get("data") if isinstance(valuation.get("data"), Mapping) else {}
    growth_data = growth.get("data") if isinstance(growth.get("data"), Mapping) else {}
    earnings_data = earnings.get("data") if isinstance(earnings.get("data"), Mapping) else {}
    earnings_public: dict[str, Any] = {}
    for section, keys in {
        "financial_report": ("report_date", "revenue", "net_profit_parent", "operating_cash_flow", "roe", "currency"),
        "dividend": ("ttm_event_count", "ttm_cash_dividend_per_share", "ttm_dividend_yield_pct", "currency", "as_of"),
    }.items():
        section_data = earnings_data.get(section)
        metrics = _safe_metric_map(section_data, keys)
        if metrics:
            earnings_public[section] = metrics
    return {
        **_overview_block(result, "fundamentals", thai_output=thai_output),
        "status": _safe_text(context.get("status"), 32) or _overview_block(result, "fundamentals", thai_output=thai_output).get("status"),
        "as_of": _safe_text(context.get("as_of"), 64),
        "summary": _public_text(_value(result, "fundamental_analysis"), thai_output=thai_output),
        "sector": _public_text(_value(result, "sector_position"), thai_output=thai_output),
        "company": _public_text(_value(result, "company_highlights"), thai_output=thai_output),
        "valuation": _safe_metric_map(valuation_data, ("pe_ratio", "pb_ratio", "total_mv", "circ_mv")),
        "growth": _safe_metric_map(growth_data, ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin")),
        "earnings": earnings_public,
    }


def _news_payload(result: Any, *, thai_output: bool) -> dict[str, Any]:
    overview = _value(result, "analysis_context_pack_overview")
    metadata = overview.get("metadata") if isinstance(overview, Mapping) else {}
    count = metadata.get("news_result_count") if isinstance(metadata, Mapping) else None
    return {
        **_overview_block(result, "news", thai_output=thai_output),
        "summary": _public_text(_value(result, "news_summary"), thai_output=thai_output),
        "sentiment": _public_text(_value(result, "market_sentiment"), thai_output=thai_output),
        "topics": _public_text(_value(result, "hot_topics"), thai_output=thai_output),
        "search_performed": bool(_value(result, "search_performed", False)),
        "result_count": int(count) if isinstance(count, int) and count >= 0 else None,
    }


def _dashboard(result: Any) -> dict[str, Any]:
    dashboard = _value(result, "dashboard", {})
    return dashboard if isinstance(dashboard, Mapping) else {}


def _market_for_ticker(ticker: str) -> str:
    code = ticker.upper()
    if code.endswith(".HK") or code.startswith("HK"):
        return "hk_stock"
    if code.endswith(".TW") or code.endswith(".TWO"):
        return "tw_stock"
    if code.isdigit() and len(code) in (4, 5, 6):
        return "cn_stock"
    return "us_stock"


def _quality(result: Any, dashboard: Mapping[str, Any]) -> dict[str, Any]:
    overview = _value(result, "analysis_context_pack_overview")
    candidates = [
        _value(result, "data_quality"),
        overview.get("data_quality") if isinstance(overview, Mapping) else None,
        dashboard.get("data_quality"),
        dashboard.get("analysis_context_pack_overview", {}).get("data_quality")
        if isinstance(dashboard.get("analysis_context_pack_overview"), Mapping)
        else None,
        _value(result, "market_snapshot"),
    ]
    quality: Mapping[str, Any] = {}
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            quality = candidate
            break
    stale = quality.get("stale")
    if not isinstance(stale, bool):
        stale = None
    bar_asof = (
        quality.get("bar_asof")
        or quality.get("effective_bar_date")
        or quality.get("asof")
        or quality.get("date")
    )
    return {"stale": stale, "bar_asof": _safe_text(bar_asof, 64)}


def _stock_payload(result: Any) -> dict[str, Any]:
    dashboard = _dashboard(result)
    core = dashboard.get("core_conclusion")
    core = core if isinstance(core, Mapping) else {}
    intelligence = dashboard.get("intelligence")
    intelligence = intelligence if isinstance(intelligence, Mapping) else {}
    phase = dashboard.get("phase_decision")
    phase = phase if isinstance(phase, Mapping) else {}

    ticker = _safe_text(_value(result, "code"), 64) or "unknown"
    success = bool(_value(result, "success", True))
    thai_output = str(_value(result, "report_language") or "").strip().lower() == "th"
    technical = _technical_payload(result, thai_output=thai_output)
    fundamentals = _fundamentals_payload(result, thai_output=thai_output)
    news = _news_payload(result, thai_output=thai_output)
    risks = _thai_items(intelligence.get("risk_alerts")) if thai_output else _items(intelligence.get("risk_alerts"))
    warning_items = _thai_items(_value(result, "risk_warning")) if thai_output else _items(_value(result, "risk_warning"))
    for risk in warning_items:
        if risk not in risks:
            risks.append(risk)
    unknowns = (
        _thai_items(
            phase.get("data_limitations"),
            fallback="ข้อจำกัดของข้อมูล: ยังมีข้อมูลบางส่วนที่ยืนยันไม่ได้",
        )
        if thai_output
        else _items(phase.get("data_limitations"))
    )
    error_message = _safe_text(_value(result, "error_message"), 500)
    if not success and error_message and error_message not in unknowns:
        unknowns.append(error_message)

    summary = (
        _safe_text(core.get("one_sentence"), 800)
        or _safe_text(_value(result, "analysis_summary"), 800)
        or "ยังไม่มีสรุปจาก AI"
    )
    if thai_output:
        summary = _thai_text(summary, "ยังไม่มีสรุปภาษาไทยจาก AI") or "ยังไม่มีสรุปภาษาไทยจาก AI"
    score = _value(result, "sentiment_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        score = None

    return {
        "ticker": ticker,
        "name": _safe_text(_value(result, "name"), 160) or ticker,
        "market": _market_for_ticker(ticker),
        "status": "ok" if success else "unavailable",
        "decision": _safe_text(
            _value(result, "action") or _value(result, "decision_type"), 32
        ),
        "action_label": _safe_text(_value(result, "action_label"), 80),
        "score": score,
        "trend": (
            _thai_text(_value(result, "trend_prediction"), "ยังไม่ทราบ")
            if thai_output
            else _safe_text(_value(result, "trend_prediction"), 120)
        ),
        "confidence": (
            _thai_text(_value(result, "confidence_level"), "ยังไม่ทราบ")
            if thai_output
            else _safe_text(_value(result, "confidence_level"), 40)
        ),
        "summary_th": summary,
        "catalysts": (
            _thai_items(intelligence.get("positive_catalysts"))
            if thai_output
            else _items(intelligence.get("positive_catalysts"))
        ),
        "risks": risks[:5],
        "unknowns": unknowns[:5],
        "technical": technical,
        "fundamentals": fundamentals,
        "news": news,
        "data_quality": _quality(result, dashboard),
        "data_sources": _safe_text(_value(result, "data_sources"), 600),
        "search_performed": bool(_value(result, "search_performed", False)),
        "model_used": _safe_text(_value(result, "model_used"), 160),
    }


def build_public_daily_payload(
    results: Iterable[Any],
    *,
    report_date: Optional[date | datetime | str] = None,
) -> dict[str, Any]:
    stocks = [_stock_payload(result) for result in results if result is not None]
    if isinstance(report_date, datetime):
        date_text = report_date.date().isoformat()
    elif isinstance(report_date, date):
        date_text = report_date.isoformat()
    elif report_date:
        date_text = str(report_date)
    else:
        date_text = date.today().isoformat()

    models = [stock["model_used"] for stock in stocks if stock["model_used"]]
    providers = list(dict.fromkeys(
        model.split("/", 1)[0]
        for model in models
        if "/" in model
    ))
    any_success = any(stock["status"] == "ok" for stock in stocks)
    ai_status = "ok" if any_success and models else "fallback" if any_success else "unavailable"

    commercial = os.getenv("FINSANCE_COMMERCIAL_MODE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    commercial_provider = _safe_text(os.getenv("FINSANCE_COMMERCIAL_PROVIDER"), 120)
    publication_enabled = os.getenv(
        "FINSANCE_PUBLICATION_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    blockers = ["provider_licensing_review", "compliance_review", "production_verification"]
    if commercial and not commercial_provider:
        blockers.append("commercial_provider_not_configured")

    return {
        "schema_version": SCHEMA_VERSION,
        "date": date_text,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
        "ai": {
            "status": ai_status,
            "provider": ",".join(providers) or None,
            "requested_order": providers,
            "fallback": providers[1] if len(providers) > 1 else None,
        },
        "data_mode": {
            "commercial": commercial,
            "provider": commercial_provider,
            "free_sources": [] if commercial else ["yfinance", "akshare", "baostock"],
        },
        "publication": {
            "status": "public_candidate" if publication_enabled else "not_live",
            "enabled": publication_enabled,
            "blockers": blockers,
        },
        "disclaimer": "ข้อมูลเพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน",
    }


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_bytes(data)
    temp_path.replace(path)


def _write_with_checksum(path: Path, data: bytes) -> None:
    _write_atomic(path, data)
    digest = hashlib.sha256(data).hexdigest()
    _write_atomic(path.with_name(f"{path.name}.sha256"), f"{digest}  {path.name}\n".encode())


def write_daily_export(
    results: Iterable[Any],
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    report_date: Optional[date | datetime | str] = None,
) -> Optional[Path]:
    """Write an immutable date file plus the mutable latest pointer."""
    results = [result for result in results if result is not None]
    if not results:
        return None
    payload = build_public_daily_payload(results, report_date=report_date)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    root = Path(output_dir)
    daily_path = root / f"daily_{payload['date']}.json"
    if not daily_path.exists():
        _write_with_checksum(daily_path, data)
    elif not daily_path.with_name(f"{daily_path.name}.sha256").exists():
        _write_with_checksum(
            daily_path,
            daily_path.read_bytes(),
        )
    latest_path = root / "daily_latest.json"
    _write_with_checksum(latest_path, data)
    return latest_path


__all__ = ["SCHEMA_VERSION", "build_public_daily_payload", "write_daily_export"]
