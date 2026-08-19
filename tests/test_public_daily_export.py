"""Public Finsance export contract tests."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services.public_daily_export import build_public_daily_payload, write_daily_export


class PublicDailyExportTestCase(unittest.TestCase):
    def _result(self):
        return SimpleNamespace(
            code="600519",
            name="测试股票",
            success=True,
            action="watch",
            action_label="观察",
            decision_type="hold",
            sentiment_score=61,
            trend_prediction="震荡",
            confidence_level="中",
            analysis_summary="基于公开数据的观察摘要。",
            report_language="th",
            technical_analysis="均线仍在观察，等待更多确认。",
            trend_analysis="短线趋势偏中性。",
            ma_analysis="MA5 และ MA20 ยังไม่ตัดกันชัดเจน",
            volume_analysis="ข้อมูลปริมาณการซื้อขายมีเพียงบางส่วน",
            pattern_analysis="รูปแบบแท่งเทียนยังไม่ยืนยัน",
            fundamental_analysis="รายได้และกำไรสุทธิต้องติดตาม",
            sector_position="อุตสาหกรรมเซมิคอนดักเตอร์",
            company_highlights="ธุรกิจหน่วยความจำยังมีความผันผวน。",
            news_summary="ข่าวล่าสุดยังไม่มีสัญญาณที่ยืนยันได้。",
            market_sentiment="อารมณ์ตลาดยังผันผวน。",
            hot_topics="ผลประกอบการและวัฏจักรชิป。",
            risk_warning="ข้อมูลยังต้องยืนยันเพิ่มเติม",
            search_performed=True,
            data_sources="yfinance, earnings",
            model_used="gemini/gemini-3-flash-preview",
            analysis_context_pack_overview={
                "data_quality": {"stale": False, "bar_asof": "2026-08-14"},
                "blocks": [
                    {"key": "technical", "status": "available", "source": "technical_engine"},
                    {"key": "fundamentals", "status": "partial", "source": "yfinance"},
                    {"key": "news", "status": "available", "source": "search", "metadata": {"news_result_count": 2}},
                ],
                "metadata": {"news_result_count": 2},
            },
            market_snapshot={
                "date": "2026-08-14",
                "close": "123.45",
                "volume": "1.2M",
                "volume_ratio": "1.1",
                "turnover_rate": "0.4%",
            },
            fundamental_context={
                "status": "partial",
                "as_of": "2026-08-14T00:00:00+00:00",
                "valuation": {"status": "ok", "data": {"pe_ratio": 22.5, "pb_ratio": 1.8, "secret": "drop"}},
                "growth": {"status": "partial", "data": {"revenue_yoy": 12.3, "roe": 8.4}},
                "earnings": {"status": "ok", "data": {"financial_report": {"revenue": 100}}},
            },
            current_price=123.45,
            raw_response='{"secret":"do-not-publish"}',
            dashboard={
                "core_conclusion": {"one_sentence": "等待更多确认。"},
                "intelligence": {
                    "positive_catalysts": ["公开催化"],
                    "risk_alerts": ["公开风险"],
                },
                "phase_decision": {"data_limitations": ["bar freshness unavailable"]},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "123.45",
                        "stop_loss": "100",
                        "take_profit": "150",
                    }
                },
            },
        )

    def test_allow_list_excludes_private_fields_and_writes_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {
                "FINSANCE_PUBLICATION_ENABLED": "false",
                "FINSANCE_COMMERCIAL_MODE": "false",
            },
            clear=False,
        ):
            path = write_daily_export(
                [self._result()],
                output_dir=Path(temp_dir),
                report_date="2026-08-14",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            dumped = json.dumps(payload, ensure_ascii=False)

            self.assertEqual(payload["schema_version"], "finsance-quanttrade-daily-v1")
            self.assertEqual(payload["stocks"][0]["ticker"], "600519")
            self.assertFalse(payload["stocks"][0]["data_quality"]["stale"])
            self.assertEqual(payload["stocks"][0]["data_quality"]["bar_asof"], "2026-08-14")
            self.assertEqual(payload["ai"]["requested_order"], ["gemini"])
            self.assertIsNone(payload["ai"]["fallback"])
            self.assertNotIn("battle_plan", dumped)
            self.assertNotIn("current_price", dumped)
            self.assertNotIn("123.45", dumped)
            self.assertEqual(payload["stocks"][0]["technical"]["status"], "available")
            self.assertEqual(payload["stocks"][0]["technical"]["moving_average"], "MA5 และ MA20 ยังไม่ตัดกันชัดเจน")
            self.assertEqual(payload["stocks"][0]["fundamentals"]["valuation"], {"pe_ratio": 22.5, "pb_ratio": 1.8})
            self.assertEqual(payload["stocks"][0]["fundamentals"]["earnings"]["financial_report"], {"revenue": 100.0})
            self.assertEqual(payload["stocks"][0]["technical"]["snapshot"], {
                "date": "2026-08-14",
                "volume": "1.2M",
                "volume_ratio": "1.1",
                "turnover_rate": "0.4%",
            })
            self.assertNotIn("secret", dumped)
            self.assertEqual(payload["stocks"][0]["news"]["result_count"], 2)
            self.assertTrue(path.with_name(f"{path.name}.sha256").exists())
            self.assertTrue(Path(temp_dir, "daily_2026-08-14.json").exists())

    def test_empty_results_do_not_publish(self):
        payload = build_public_daily_payload([])
        self.assertEqual(payload["stocks"], [])
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(write_daily_export([], output_dir=temp_dir))

    def test_thai_public_text_does_not_leak_untranslated_cjk(self):
        result = self._result()
        result.report_language = "th"
        result.trend_prediction = "看多"
        result.confidence_level = "低"
        result.analysis_summary = "รอดู"
        result.risk_warning = "资金流数据缺失"
        payload = build_public_daily_payload([result])
        stock = payload["stocks"][0]

        self.assertEqual(stock["trend"], "ขาขึ้น")
        self.assertEqual(stock["confidence"], "ต่ำ")
        self.assertNotRegex(stock["summary_th"], r"[\u3400-\u9fff]")
        self.assertTrue(all(not re.search(r"[\u3400-\u9fff]", item) for item in stock["risks"]))
        self.assertTrue(all(not re.search(r"[\u3400-\u9fff]", item) for item in stock["unknowns"]))

    def test_thai_public_sections_drop_untranslated_cjk(self):
        result = self._result()
        result.technical_analysis = "技术面分析"
        result.fundamental_analysis = "基本面分析"
        result.news_summary = "新闻摘要"
        payload = build_public_daily_payload([result])
        stock = payload["stocks"][0]

        self.assertIsNone(stock["technical"]["summary"])
        self.assertIsNone(stock["fundamentals"]["summary"])
        self.assertIsNone(stock["news"]["summary"])


if __name__ == "__main__":
    unittest.main()
