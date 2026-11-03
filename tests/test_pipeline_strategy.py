"""The writer and the editor must agree on which editorial strategy was used.

Research can introduce conflicts, which change the recommendation. The pipeline
used to resolve the strategy from the topic row loaded *before* research ran, so
the draft followed one strategy while the editor displayed another.
"""

from __future__ import annotations

from modules import strategy
from modules.database import Database
from modules.pipeline import Pipeline


class StubResearcher:
    """Writes a completed research report with one conflicting claim."""

    def __init__(self, database: Database):
        self.db = database

    def research(self, topic_id: int, should_cancel=None, progress=None, force: bool = False):
        research_id = self.db.create_research(
            {
                "topic_id": topic_id,
                "status": "complete",
                "confidence": 62.0,
                "confidence_label": "mid",
                "source_count": 2,
                "model": "stub",
                "data": {
                    "summary": "Tóm tắt nghiên cứu.",
                    "key_facts": [
                        {"fact": "Sự việc xảy ra ngày 2/11.", "verdict": "confirmed"},
                    ],
                    "conflicting": ["Hai nguồn nêu số người bị ảnh hưởng khác nhau."],
                    "unconfirmed": [],
                    "confidence": 62,
                },
            }
        )
        return self.db.get_latest_research(topic_id) or {"id": research_id}


class StubWriter:
    """Captures the options the pipeline resolved and stores a post."""

    def __init__(self, database: Database):
        self.db = database
        self.options: dict | None = None

    def write(self, topic_id, research_id=None, options=None, should_cancel=None, progress=None):
        self.options = dict(options or {})
        post_id = self.db.create_post(
            {
                "topic_id": topic_id,
                "research_id": research_id,
                "headline": "Bài kiểm tra",
                "main_post": "Nội dung kiểm tra.",
                "style": self.options.get("style"),
                "tone": self.options.get("tone"),
                "length": self.options.get("length"),
            }
        )
        return self.db.get_post(post_id)


def test_write_resolves_strategy_after_research(temp_db):
    topic_id = temp_db.create_topic(
        {
            "title": "Chip mới của một hãng công nghệ",
            "category": "technology",
            "score": 75.0,
            "source_count": 2,
            "published_at": "2026-11-03T08:00:00",
            "last_seen_at": "2026-11-03T08:30:00",
        }
    )

    topic = temp_db.get_topic(topic_id)
    before_research = strategy.resolve(topic, topic.get("research"), None, None)
    assert before_research["effective_key"] == strategy.STRATEGY_TECH

    researcher = StubResearcher(temp_db)
    writer = StubWriter(temp_db)
    pipeline = Pipeline(database=temp_db, researcher=researcher, writer=writer)
    pipeline.write(topic_id, {}, research_first=True)

    topic = temp_db.get_topic(topic_id)
    after_research = strategy.resolve(topic, topic.get("research"), None, None)
    assert after_research["effective_key"] == strategy.STRATEGY_SENSITIVE

    assert writer.options["strategy_key"] == strategy.STRATEGY_SENSITIVE
    definition = strategy.STRATEGIES[strategy.STRATEGY_SENSITIVE]
    assert writer.options["tone"] == definition["tone"]
    assert writer.options["audience"] == definition["audience"]
    assert writer.options["style"] == definition["style"]


def test_write_keeps_explicit_user_options(temp_db):
    """An explicit style chosen by the user is not silently replaced."""
    topic_id = temp_db.create_topic(
        {"title": "Tin công nghệ", "category": "technology", "score": 70.0}
    )
    writer = StubWriter(temp_db)
    pipeline = Pipeline(database=temp_db, researcher=StubResearcher(temp_db), writer=writer)
    pipeline.write(topic_id, {"style": "conversational"}, research_first=True)

    assert writer.options["style"] == "conversational"
    # The strategy key still reflects the research-driven recommendation.
    assert writer.options["strategy_key"] == strategy.STRATEGY_SENSITIVE
