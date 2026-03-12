from __future__ import annotations

from dataclasses import dataclass

from src.data.news import NewsArticle
from src.storage.models import SentimentRecord


@dataclass
class SentimentResult:
    label: str  # "positive", "negative", "neutral"
    score: float  # confidence 0-1
    normalized_score: float  # -1 to +1 (negative=-1, neutral=0, positive=+1)


class SentimentAnalyzer:
    """Scores financial text sentiment using FinBERT.

    Lazily loads the model on first use to avoid slow imports
    and large memory allocation when not needed (e.g., in tests).
    """

    def __init__(self):
        self._pipeline = None

    def _load_model(self):
        if self._pipeline is None:
            from transformers import pipeline

            self._pipeline = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
            )

    def score_text(self, text: str) -> SentimentResult:
        self._load_model()
        result = self._pipeline(text, truncation=True, max_length=512)[0]

        label = result["label"].lower()
        confidence = result["score"]

        if label == "positive":
            normalized = confidence
        elif label == "negative":
            normalized = -confidence
        else:
            normalized = 0.0

        return SentimentResult(
            label=label,
            score=confidence,
            normalized_score=normalized,
        )

    def score_headline(self, article: NewsArticle) -> SentimentRecord:
        result = self.score_text(article.headline)
        return SentimentRecord(
            ticker=article.ticker,
            headline=article.headline,
            source=article.source,
            score=result.normalized_score,
            timestamp=article.published_at,
        )

    def score_articles(self, articles: list[NewsArticle]) -> list[SentimentRecord]:
        return [self.score_headline(a) for a in articles]

    def aggregate_sentiment(self, records: list[SentimentRecord]) -> float:
        """Returns average normalized sentiment score across records.

        Returns 0.0 if no records provided.
        """
        if not records:
            return 0.0
        return sum(r.score for r in records) / len(records)
