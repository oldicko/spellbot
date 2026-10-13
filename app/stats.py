"""Adult figures: average, trend, and whether each word is learned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.spelling import status_name, streak_of, trend
from app.store import ScoreRow, Store

TREND_LABELS = {
    "up": "Going up",
    "down": "Dipping",
    "steady": "Holding steady",
    "early": "Too soon to tell",
}
STATUS_LABELS = {
    "learned": "Learned",
    "practising": "Practising",
    "not_started": "Not started",
}


@dataclass
class WordStat:
    word: str
    lists: list[str]
    status: str
    attempts: int
    correct: int
    streak: int
    last_wrong: str | None


@dataclass
class Report:
    average_points: int | None
    recent_points: int | None
    trend: str
    scores: list[ScoreRow]
    words: list[WordStat]


def build_report(store: Store, child_id: int, now: datetime) -> Report:
    store.close_expired(now, child_id)
    scores = store.score_rows(child_id)
    sitting_points = [row.points for row in scores]
    return Report(
        average_points=_average(sitting_points),
        recent_points=_average(sitting_points[-5:]),
        trend=trend(sitting_points),
        scores=scores,
        words=_word_stats(store, child_id),
    )


def _word_stats(store: Store, child_id: int) -> list[WordStat]:
    grouped: dict[str, dict] = {}
    for word, list_name in store.assigned_words(child_id):
        slot = grouped.get(word.word_key)
        if slot is None:
            grouped[word.word_key] = {"word": word.word, "lists": [list_name]}
        elif list_name not in slot["lists"]:
            slot["lists"].append(list_name)
    history = store.history(child_id)
    stats: list[WordStat] = []
    for key, slot in grouped.items():
        tries = history.get(key, [])
        flags = [item[0] for item in tries]
        last_wrong = tries[-1][1] if tries and not tries[-1][0] else None
        stats.append(
            WordStat(
                word=slot["word"],
                lists=sorted(slot["lists"], key=str.casefold),
                status=status_name(flags),
                attempts=len(flags),
                correct=sum(1 for flag in flags if flag),
                streak=streak_of(flags),
                last_wrong=last_wrong,
            )
        )
    stats.sort(key=_sort_key)
    return stats


def _sort_key(stat: WordStat) -> tuple:
    status_order = {"practising": 0, "not_started": 1, "learned": 2}
    missed = 0 if stat.last_wrong else 1
    accuracy = stat.correct / stat.attempts if stat.attempts else 1
    return (status_order[stat.status], missed, accuracy, stat.word.casefold())


def _average(values: list[int]) -> int | None:
    if not values:
        return None
    return (sum(values) + len(values) // 2) // len(values)
