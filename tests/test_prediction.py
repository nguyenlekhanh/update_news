"""Trend prediction: momentum, confidence, opportunity and labelled forecasts."""

from __future__ import annotations

from datetime import timedelta

from modules import prediction, viral
from modules.utils import utcnow


def history(scores, gap_hours: int = 6) -> list[dict]:
    now = utcnow()
    count = len(scores)
    return [
        {
            "score": float(score),
            "captured_at": (now - timedelta(hours=(count - 1 - index) * gap_hours)).isoformat(),
            "source_count": 2,
            "independent_domains": 2,
        }
        for index, score in enumerate(scores)
    ]


def test_accelerating_momentum_is_detected_from_history():
    result = prediction.momentum(history([40, 44, 49, 56, 64]))

    assert result["label"] == prediction.MOMENTUM_ACCELERATING
    assert result["per_6h"] >= 2
    assert result["kind"] == "measured"
    assert result["snapshots"] == 5


def test_snapshots_minutes_apart_do_not_explode_momentum():
    now = utcnow()
    rows = [
        {"score": 50.0, "captured_at": (now - timedelta(minutes=10)).isoformat()},
        {"score": 52.0, "captured_at": now.isoformat()},
    ]
    result = prediction.momentum(rows)

    assert result["per_6h"] is None
    assert result["per_run"] == 2.0
    assert result["window"] == "short"
    assert result["label"] == prediction.MOMENTUM_RISING


def test_falling_history_is_reported_as_fading():
    result = prediction.momentum(history([80, 78, 70, 64, 58]))

    assert result["label"] == prediction.MOMENTUM_FADING
    assert result["per_6h"] <= -2


def test_single_snapshot_cannot_have_momentum():
    result = prediction.momentum(history([70]))

    assert result["label"] == prediction.MOMENTUM_UNKNOWN
    assert result["kind"] == "unavailable"
    assert result["value"] is None


def test_confidence_stays_low_with_only_two_readings():
    result = prediction.confidence(history([50, 60]))

    assert result["value"] <= 35
    assert result["label"] == "low"
    assert result["kind"] == "estimated"


def test_confidence_improves_with_more_snapshots():
    few = prediction.confidence(history([50, 60]))
    many = prediction.confidence(history([40, 44, 49, 56, 64, 70]))

    assert many["value"] > few["value"]
    assert many["snapshots"] == 6


def test_forecast_needs_three_points_and_labels_itself_estimated():
    missing = prediction.forecast(history([50, 60]), {"value": 90})
    assert missing["available"] is False
    assert missing["next_score"] is None

    confident = prediction.confidence(history([40, 46, 52, 60]))
    result = prediction.forecast(history([40, 46, 52, 60]), confident)

    assert result["available"] is True
    assert result["kind"] == "estimated"
    assert result["next_score"] > 60
    assert result["direction"] == "up"


def test_opportunity_prefers_fresh_topics_over_stale_ones():
    stale = history([60, 62, 64])
    values = {}
    for hours in (4.0, 80.0):
        intel = prediction.momentum(stale)
        values[hours] = prediction.opportunity(
            {"score": 64, "source_count": 3}, stale, intel, hours, 3
        )
    assert values[4.0]["score"] > values[80.0]["score"]
    assert values[4.0]["kind"] == "estimated"
    assert values[4.0]["publish_window_hours"] > 0


def test_history_compare_reports_peak_and_change():
    result = prediction.history_compare(history([40, 70, 55]))

    assert result["runs"] == 3
    assert result["peak_score"] == 70
    assert result["total_change"] == 15
    assert result["trend"] == "up"


def test_analyze_bundle_is_internally_consistent():
    rows = history([45, 50, 58, 63])
    topic = {"score": 63, "source_count": 3}
    result = prediction.analyze(topic, rows, freshness_hours=5, independent_domains=3)

    assert result["current_score"] == 63
    assert result["previous_score"] == 58
    assert result["score_change"] == 5
    assert result["lifecycle"]["accelerating"] in (True, False)
    assert result["disclaimer"]
    assert all(point["score"] is not None for point in result["series"])


def test_trend_shift_compares_current_window_with_previous():
    rows = []
    for topic_id, scores in ((1, [50, 60]), (2, [70, 62]), (3, [40, 40.5])):
        for index, score in enumerate(scores):
            rows.append(
                {
                    "topic_id": topic_id,
                    "score": score,
                    "captured_at": (utcnow() - timedelta(hours=12 * (1 - index))).isoformat(),
                }
            )
    result = prediction.trend_shift(rows, [{"id": 1, "title": "A"}], window_hours=24)

    assert result["rising"] == 1
    assert result["falling"] == 1
    assert result["stable"] == 1
    assert result["top_rising"][0]["topic_id"] == 1
    assert result["kind"] == "measured"


def test_phase_helper_still_consumed_for_lifecycle():
    rows = history([40, 50, 60])
    assert viral.phase_of(rows)["phase"] in viral.PHASES
