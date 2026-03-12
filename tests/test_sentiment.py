from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.analysis.sentiment import SentimentAnalyzer, SentimentResult
from src.data.news import NewsArticle
from src.storage.models import SentimentRecord


def _make_article(**overrides) -> NewsArticle:
    defaults = {
        "headline": "Apple reports record revenue",
        "summary": "Revenue was great.",
        "source": "reuters",
        "ticker": "AAPL",
        "url": "https://example.com",
        "published_at": "2025-06-01T10:00:00Z",
    }
    defaults.update(overrides)
    return NewsArticle(**defaults)


class TestSentimentResult:
    def test_positive_result(self):
        result = SentimentResult(label="positive", score=0.95, normalized_score=0.95)
        assert result.label == "positive"
        assert result.normalized_score > 0

    def test_negative_result(self):
        result = SentimentResult(label="negative", score=0.85, normalized_score=-0.85)
        assert result.label == "negative"
        assert result.normalized_score < 0

    def test_neutral_result(self):
        result = SentimentResult(label="neutral", score=0.70, normalized_score=0.0)
        assert result.normalized_score == 0.0


class TestSentimentAnalyzer:
    @patch("src.analysis.sentiment.SentimentAnalyzer._load_model")
    def test_score_text_positive(self, mock_load):
        analyzer = SentimentAnalyzer()
        analyzer._pipeline = MagicMock()
        analyzer._pipeline.return_value = [{"label": "positive", "score": 0.95}]

        result = analyzer.score_text("Company reports record earnings")
        assert result.label == "positive"
        assert result.normalized_score == 0.95

    @patch("src.analysis.sentiment.SentimentAnalyzer._load_model")
    def test_score_text_negative(self, mock_load):
        analyzer = SentimentAnalyzer()
        analyzer._pipeline = MagicMock()
        analyzer._pipeline.return_value = [{"label": "negative", "score": 0.88}]

        result = analyzer.score_text("Company faces major lawsuit")
        assert result.label == "negative"
        assert abs(result.normalized_score - (-0.88)) < 0.001

    @patch("src.analysis.sentiment.SentimentAnalyzer._load_model")
    def test_score_text_neutral(self, mock_load):
        analyzer = SentimentAnalyzer()
        analyzer._pipeline = MagicMock()
        analyzer._pipeline.return_value = [{"label": "neutral", "score": 0.72}]

        result = analyzer.score_text("Company holds annual meeting")
        assert result.label == "neutral"
        assert result.normalized_score == 0.0

    @patch("src.analysis.sentiment.SentimentAnalyzer._load_model")
    def test_score_headline(self, mock_load):
        analyzer = SentimentAnalyzer()
        analyzer._pipeline = MagicMock()
        analyzer._pipeline.return_value = [{"label": "positive", "score": 0.90}]

        article = _make_article(headline="Apple launches amazing product")
        record = analyzer.score_headline(article)

        assert isinstance(record, SentimentRecord)
        assert record.ticker == "AAPL"
        assert record.headline == "Apple launches amazing product"
        assert record.source == "reuters"
        assert record.score == 0.90

    @patch("src.analysis.sentiment.SentimentAnalyzer._load_model")
    def test_score_articles(self, mock_load):
        analyzer = SentimentAnalyzer()
        analyzer._pipeline = MagicMock()
        analyzer._pipeline.side_effect = [
            [{"label": "positive", "score": 0.9}],
            [{"label": "negative", "score": 0.8}],
        ]

        articles = [
            _make_article(headline="Good news"),
            _make_article(headline="Bad news"),
        ]
        records = analyzer.score_articles(articles)

        assert len(records) == 2
        assert records[0].score == 0.9
        assert records[1].score == -0.8

    def test_aggregate_sentiment_positive(self):
        analyzer = SentimentAnalyzer()
        records = [
            SentimentRecord("AAPL", "H1", "src", 0.8, "2025-01-01"),
            SentimentRecord("AAPL", "H2", "src", 0.6, "2025-01-01"),
            SentimentRecord("AAPL", "H3", "src", 0.4, "2025-01-01"),
        ]
        avg = analyzer.aggregate_sentiment(records)
        assert abs(avg - 0.6) < 0.001

    def test_aggregate_sentiment_mixed(self):
        analyzer = SentimentAnalyzer()
        records = [
            SentimentRecord("AAPL", "H1", "src", 0.8, "2025-01-01"),
            SentimentRecord("AAPL", "H2", "src", -0.6, "2025-01-01"),
        ]
        avg = analyzer.aggregate_sentiment(records)
        assert abs(avg - 0.1) < 0.001

    def test_aggregate_sentiment_empty(self):
        analyzer = SentimentAnalyzer()
        assert analyzer.aggregate_sentiment([]) == 0.0

    @patch("src.analysis.sentiment.SentimentAnalyzer._load_model")
    def test_lazy_loading(self, mock_load):
        analyzer = SentimentAnalyzer()
        assert analyzer._pipeline is None
        # _load_model is called on first score_text
        analyzer._pipeline = MagicMock()
        analyzer._pipeline.return_value = [{"label": "neutral", "score": 0.5}]
        analyzer.score_text("test")
        # Pipeline should now be set
        assert analyzer._pipeline is not None
