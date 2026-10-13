"""SQLite storage. One family, one file, one transaction per action."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.spelling import (
    ChoiceWord,
    Letter,
    ParsedWord,
    choose_word,
    clean_attempt,
    colour_attempt,
    display_name,
    normalise,
    turn_points,
    valid_name,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    session_minutes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS word_lists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    list_id INTEGER NOT NULL REFERENCES word_lists(id) ON DELETE CASCADE,
    child_id INTEGER NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    PRIMARY KEY (list_id, child_id)
);

CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES word_lists(id) ON DELETE CASCADE,
    word TEXT NOT NULL,
    word_key TEXT NOT NULL,
    sentence TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE (list_id, word_key)
);

CREATE TABLE IF NOT EXISTS sittings (
    id INTEGER PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    child_id INTEGER NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    finished_at TEXT,
    correct_count INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    current_word_id INTEGER REFERENCES words(id) ON DELETE SET NULL,
    pending_try TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    sitting_id INTEGER NOT NULL REFERENCES sittings(id) ON DELETE CASCADE,
    word_id INTEGER REFERENCES words(id) ON DELETE SET NULL,
    word_key TEXT NOT NULL,
    word_text TEXT NOT NULL,
    first_try TEXT NOT NULL,
    first_correct INTEGER NOT NULL,
    second_try TEXT,
    second_correct INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sittings_child ON sittings(child_id, id);
CREATE INDEX IF NOT EXISTS idx_turns_sitting ON turns(sitting_id, id);
"""


class StoreError(Exception):
    pass


@dataclass
class Child:
    id: int
    name: str
    session_minutes: int


@dataclass
class WordList:
    id: int
    name: str


@dataclass
class ListSummary:
    id: int
    name: str
    word_count: int
    children: list[str]


@dataclass
class Word:
    id: int
    list_id: int
    word: str
    word_key: str
    sentence: str
    position: int


@dataclass
class Sitting:
    id: int
    token: str
    child_id: int
    started_at: datetime
    ends_at: datetime
    finished_at: datetime | None
    correct_count: int
    attempt_count: int
    current_word_id: int | None
    pending_try: str | None


@dataclass
class ScoreRow:
    id: int
    started_at: datetime
    correct_count: int
    attempt_count: int
    points: int


@dataclass
class AnswerResult:
    kind: str
    token: str
    message: str = ""
    pause_ms: int = 0
    letters: list[Letter] = field(default_factory=list)
    note: str | None = None
    prefill: str = ""
    reveal_word: str | None = None
    reveal_sentence: str | None = None
    reveal_key: str | None = None
    reveal_tone: str = "show"
    next_word: ChoiceWord | None = None
    remaining_seconds: int = 0
    attempt_count: int = 0
    points: int = 0


def dump_dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def load_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _minutes(value: int) -> int:
    if value < 1 or value > 60:
        raise StoreError("Minutes need to be between 1 and 60.")
    return value


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def migrate(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_children(self) -> list[Child]:
        with self.session() as conn:
            rows = conn.execute("SELECT * FROM children ORDER BY name COLLATE NOCASE").fetchall()
        return [_child(row) for row in rows]

    def get_child(self, child_id: int) -> Child | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM children WHERE id = ?", (child_id,)).fetchone()
        return _child(row) if row else None

    def add_child(self, name: str, minutes: int, now: datetime) -> Child:
        clean = _checked_name(name)
        minutes = _minutes(minutes)
        try:
            with self.session() as conn:
                cursor = conn.execute(
                    "INSERT INTO children (name, session_minutes, created_at) VALUES (?, ?, ?)",
                    (clean, minutes, dump_dt(now)),
                )
                child_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise StoreError("There is already a child with that name.") from exc
        child = self.get_child(child_id)
        assert child is not None
        return child

    def update_child(self, child_id: int, name: str, minutes: int) -> None:
        clean = _checked_name(name)
        minutes = _minutes(minutes)
        try:
            with self.session() as conn:
                cursor = conn.execute(
                    "UPDATE children SET name = ?, session_minutes = ? WHERE id = ?",
                    (clean, minutes, child_id),
                )
                if cursor.rowcount == 0:
                    raise StoreError("That child is not on the list.")
        except sqlite3.IntegrityError as exc:
            raise StoreError("There is already a child with that name.") from exc

    def delete_child(self, child_id: int) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM children WHERE id = ?", (child_id,))

    def list_word_lists(self) -> list[ListSummary]:
        with self.session() as conn:
            lists = conn.execute("SELECT * FROM word_lists ORDER BY name COLLATE NOCASE").fetchall()
            counts = {
                row["list_id"]: row["n"]
                for row in conn.execute("SELECT list_id, COUNT(*) AS n FROM words GROUP BY list_id")
            }
            names: dict[int, list[str]] = {}
            assigned = conn.execute(
                """
                SELECT a.list_id, c.name
                FROM assignments a
                JOIN children c ON c.id = a.child_id
                ORDER BY c.name COLLATE NOCASE
                """
            )
            for row in assigned:
                names.setdefault(row["list_id"], []).append(row["name"])
        return [
            ListSummary(
                id=row["id"],
                name=row["name"],
                word_count=counts.get(row["id"], 0),
                children=names.get(row["id"], []),
            )
            for row in lists
        ]

    def get_word_list(self, list_id: int) -> WordList | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM word_lists WHERE id = ?", (list_id,)).fetchone()
        return WordList(id=row["id"], name=row["name"]) if row else None

    def add_word_list(self, name: str, now: datetime) -> WordList:
        clean = _checked_list_name(name)
        try:
            with self.session() as conn:
                cursor = conn.execute(
                    "INSERT INTO word_lists (name, created_at) VALUES (?, ?)",
                    (clean, dump_dt(now)),
                )
                list_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise StoreError("There is already a list with that name.") from exc
        found = self.get_word_list(list_id)
        assert found is not None
        return found

    def rename_word_list(self, list_id: int, name: str) -> None:
        clean = _checked_list_name(name)
        try:
            with self.session() as conn:
                cursor = conn.execute("UPDATE word_lists SET name = ? WHERE id = ?", (clean, list_id))
                if cursor.rowcount == 0:
                    raise StoreError("That list is not here.")
        except sqlite3.IntegrityError as exc:
            raise StoreError("There is already a list with that name.") from exc

    def delete_word_list(self, list_id: int) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM word_lists WHERE id = ?", (list_id,))

    def assignment_ids(self, list_id: int) -> set[int]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT child_id FROM assignments WHERE list_id = ?",
                (list_id,),
            ).fetchall()
        return {row["child_id"] for row in rows}

    def set_assignments(self, list_id: int, child_ids: list[int]) -> None:
        with self.session() as conn:
            if conn.execute("SELECT 1 FROM word_lists WHERE id = ?", (list_id,)).fetchone() is None:
                raise StoreError("That list is not here.")
            known = {row["id"] for row in conn.execute("SELECT id FROM children")}
            chosen = [child_id for child_id in dict.fromkeys(child_ids) if child_id in known]
            conn.execute("DELETE FROM assignments WHERE list_id = ?", (list_id,))
            conn.executemany(
                "INSERT INTO assignments (list_id, child_id) VALUES (?, ?)",
                [(list_id, child_id) for child_id in chosen],
            )

    def words_in_list(self, list_id: int) -> list[Word]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM words WHERE list_id = ? ORDER BY position, id",
                (list_id,),
            ).fetchall()
        return [_word(row) for row in rows]

    def spellings(self) -> list[str]:
        """One display spelling per word, across every list."""
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT word FROM words
                WHERE id IN (SELECT MIN(id) FROM words GROUP BY word_key)
                ORDER BY id
                """
            ).fetchall()
        return [row["word"] for row in rows]

    def add_words(self, list_id: int, rows: list[ParsedWord]) -> tuple[int, int]:
        added = 0
        updated = 0
        with self.session() as conn:
            if conn.execute("SELECT 1 FROM word_lists WHERE id = ?", (list_id,)).fetchone() is None:
                raise StoreError("That list is not here.")
            for item in rows:
                existing = conn.execute(
                    "SELECT id FROM words WHERE list_id = ? AND word_key = ?",
                    (list_id, item.word_key),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE words SET word = ?, sentence = ? WHERE id = ?",
                        (item.word, item.sentence, existing["id"]),
                    )
                    updated += 1
                    continue
                position = conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 AS n FROM words WHERE list_id = ?",
                    (list_id,),
                ).fetchone()["n"]
                conn.execute(
                    """
                    INSERT INTO words (list_id, word, word_key, sentence, position)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (list_id, item.word, item.word_key, item.sentence, position),
                )
                added += 1
        return added, updated

    def update_sentence(self, word_id: int, sentence: str) -> None:
        clean = " ".join(sentence.split())
        if not clean or len(clean) > 240:
            raise StoreError("The sentence needs some text, up to 240 characters.")
        with self.session() as conn:
            cursor = conn.execute("UPDATE words SET sentence = ? WHERE id = ?", (clean, word_id))
            if cursor.rowcount == 0:
                raise StoreError("That word is not on the list.")

    def delete_word(self, word_id: int) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM words WHERE id = ?", (word_id,))

    def assigned_word_count(self, child_id: int) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM words w
                JOIN assignments a ON a.list_id = w.list_id
                WHERE a.child_id = ?
                """,
                (child_id,),
            ).fetchone()
        return int(row["n"])

    def open_sitting(self, child_id: int) -> Sitting | None:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT * FROM sittings
                WHERE child_id = ? AND finished_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (child_id,),
            ).fetchone()
        return _sitting(row) if row else None

    def latest_score(self, child_id: int) -> ScoreRow | None:
        rows = self.score_rows(child_id)
        return rows[-1] if rows else None

    def score_rows(self, child_id: int) -> list[ScoreRow]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT id, started_at, correct_count, attempt_count
                FROM sittings
                WHERE child_id = ? AND finished_at IS NOT NULL AND attempt_count > 0
                ORDER BY id
                """,
                (child_id,),
            ).fetchall()
            totals = self._points_by_sitting(conn, [row["id"] for row in rows])
        return [_score(row, totals[row["id"]]) for row in rows]

    def best_points(self, child_id: int) -> int | None:
        rows = self.score_rows(child_id)
        if not rows:
            return None
        return max(row.points for row in rows)

    def sitting_points(self, sitting_id: int) -> int:
        with self.session() as conn:
            return self._points(conn, sitting_id)

    def _points(self, conn, sitting_id: int) -> int:
        rows = conn.execute(
            "SELECT first_correct, second_correct FROM turns WHERE sitting_id = ?",
            (sitting_id,),
        ).fetchall()
        return _sum_points(rows)

    def _points_by_sitting(self, conn, sitting_ids: list[int]) -> dict[int, int]:
        totals = {sitting_id: 0 for sitting_id in sitting_ids}
        if not totals:
            return totals
        marks = ",".join("?" for _ in sitting_ids)
        rows = conn.execute(
            f"""
            SELECT sitting_id, first_correct, second_correct
            FROM turns
            WHERE sitting_id IN ({marks})
            """,
            sitting_ids,
        ).fetchall()
        for row in rows:
            second = None if row["second_correct"] is None else bool(row["second_correct"])
            totals[row["sitting_id"]] += turn_points(bool(row["first_correct"]), second)
        return totals

    def assigned_words(self, child_id: int) -> list[tuple[Word, str]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT w.*, l.name AS list_name
                FROM words w
                JOIN word_lists l ON l.id = w.list_id
                JOIN assignments a ON a.list_id = w.list_id
                WHERE a.child_id = ?
                ORDER BY w.id
                """,
                (child_id,),
            ).fetchall()
        return [(_word(row), row["list_name"]) for row in rows]

    def history(self, child_id: int) -> dict[str, list[tuple[bool, str]]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT t.word_key, t.first_correct, t.first_try
                FROM turns t
                JOIN sittings s ON s.id = t.sitting_id
                WHERE s.child_id = ?
                ORDER BY t.id
                """,
                (child_id,),
            ).fetchall()
        grouped: dict[str, list[tuple[bool, str]]] = {}
        for row in rows:
            grouped.setdefault(row["word_key"], []).append((bool(row["first_correct"]), row["first_try"]))
        return grouped

    def fix_count(self, sitting_id: int) -> int:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM turns
                WHERE sitting_id = ? AND first_correct = 0 AND second_correct = 1
                """,
                (sitting_id,),
            ).fetchone()
        return int(row["n"])

    def close_expired(self, now: datetime, child_id: int | None = None) -> None:
        with self.session() as conn:
            sql = "SELECT id, ends_at FROM sittings WHERE finished_at IS NULL"
            params: list[object] = []
            if child_id is not None:
                sql += " AND child_id = ?"
                params.append(child_id)
            due = [
                row["id"]
                for row in conn.execute(sql, params)
                if (loaded := load_dt(row["ends_at"])) is not None and loaded <= now
            ]
            for sitting_id in due:
                self._finish_row(conn, sitting_id, now, force=False)

    def create_sitting(self, child_id: int, now: datetime, rng) -> str | None:
        with self.session() as conn:
            child = conn.execute("SELECT * FROM children WHERE id = ?", (child_id,)).fetchone()
            if child is None:
                return None
            open_rows = conn.execute(
                "SELECT id FROM sittings WHERE child_id = ? AND finished_at IS NULL",
                (child_id,),
            ).fetchall()
            for row in open_rows:
                self._finish_row(conn, row["id"], now, force=True)
            chosen = self._choose_for_child(conn, child_id, set(), None, rng)
            if chosen is None:
                return None
            token = secrets.token_urlsafe(18)
            ends = now + timedelta(minutes=int(child["session_minutes"]))
            conn.execute(
                """
                INSERT INTO sittings (
                    token, child_id, started_at, ends_at, correct_count, attempt_count, current_word_id
                ) VALUES (?, ?, ?, ?, 0, 0, ?)
                """,
                (token, child_id, dump_dt(now), dump_dt(ends), chosen.id),
            )
        return token

    def get_sitting(self, token: str) -> Sitting | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM sittings WHERE token = ?", (token,)).fetchone()
        return _sitting(row) if row else None

    def get_word(self, word_id: int | None) -> Word | None:
        if word_id is None:
            return None
        with self.session() as conn:
            row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        return _word(row) if row else None

    def repair_sitting(self, token: str, now: datetime, rng) -> Sitting | None:
        """If the current word was deleted, move on or finish the sitting."""
        with self.session() as conn:
            row = conn.execute("SELECT * FROM sittings WHERE token = ?", (token,)).fetchone()
            if row is None:
                return None
            if row["finished_at"]:
                return _sitting(row)
            word = None
            if row["current_word_id"] is not None:
                word = conn.execute("SELECT id FROM words WHERE id = ?", (row["current_word_id"],)).fetchone()
            if word is None:
                conn.execute("UPDATE sittings SET pending_try = NULL WHERE id = ?", (row["id"],))
                dealt = self._deal(conn, row, None, now, rng)
                if dealt is None:
                    self._finish_row(conn, row["id"], now, force=True)
            refreshed = conn.execute("SELECT * FROM sittings WHERE id = ?", (row["id"],)).fetchone()
        return _sitting(refreshed) if refreshed else None

    def apply_answer(self, token: str, raw: str, now: datetime, rng) -> AnswerResult | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM sittings WHERE token = ?", (token,)).fetchone()
            if row is None:
                return None
            if row["finished_at"]:
                return AnswerResult(
                    kind="done",
                    token=token,
                    message="This sitting is over.",
                    points=self._points(conn, row["id"]),
                )
            spelling = clean_attempt(raw)
            if not spelling:
                return AnswerResult(kind="empty", token=token)
            word = None
            if row["current_word_id"] is not None:
                word = conn.execute("SELECT * FROM words WHERE id = ?", (row["current_word_id"],)).fetchone()
            if word is None:
                dealt = self._deal(conn, row, None, now, rng)
                if dealt is None:
                    self._finish_row(conn, row["id"], now, force=True)
                    return AnswerResult(
                        kind="done",
                        token=token,
                        message="This sitting is over.",
                        points=self._points(conn, row["id"]),
                    )
                return self._moved_on(conn, row, dealt, now)
            correct = normalise(spelling) == word["word_key"]
            if row["pending_try"] is None:
                if correct:
                    self._insert_turn(conn, row, word, spelling, 1, None, None, now)
                    return self._after_turn(conn, row, word, now, rng, "Yes.", 1100, "good")
                conn.execute(
                    "UPDATE sittings SET pending_try = ? WHERE id = ?",
                    (spelling, row["id"]),
                )
                letters, note = colour_attempt(spelling, word["word_key"])
                return AnswerResult(
                    kind="retry",
                    token=token,
                    message="Have another go.",
                    letters=letters,
                    note=note,
                    prefill=spelling,
                    remaining_seconds=_remaining(row, now),
                    attempt_count=int(row["attempt_count"]),
                    points=self._points(conn, row["id"]),
                )
            self._insert_turn(
                conn,
                row,
                word,
                row["pending_try"],
                0,
                spelling,
                1 if correct else 0,
                now,
            )
            if correct:
                return self._after_turn(conn, row, word, now, rng, "Got it.", 1500, "good")
            return self._after_turn(conn, row, word, now, rng, "The spelling is", 5000, "show")

    def finish(self, token: str, now: datetime) -> Sitting | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM sittings WHERE token = ?", (token,)).fetchone()
            if row is None:
                return None
            self._finish_row(conn, row["id"], now, force=True)
            refreshed = conn.execute("SELECT * FROM sittings WHERE id = ?", (row["id"],)).fetchone()
        return _sitting(refreshed) if refreshed else None

    def _choose_for_child(self, conn, child_id: int, used: set[str], last_key: str | None, rng) -> ChoiceWord | None:
        words = self._choice_words(conn, child_id)
        history = self._history_flags(conn, child_id)
        return choose_word(words, history, used, last_key, rng)

    def _choice_words(self, conn, child_id: int) -> list[ChoiceWord]:
        rows = conn.execute(
            """
            SELECT w.id, w.word_key, w.word, w.sentence
            FROM words w
            JOIN assignments a ON a.list_id = w.list_id
            WHERE a.child_id = ?
            ORDER BY w.id
            """,
            (child_id,),
        ).fetchall()
        return [
            ChoiceWord(id=row["id"], word_key=row["word_key"], word=row["word"], sentence=row["sentence"])
            for row in rows
        ]

    def _history_flags(self, conn, child_id: int) -> dict[str, list[bool]]:
        rows = conn.execute(
            """
            SELECT t.word_key, t.first_correct
            FROM turns t
            JOIN sittings s ON s.id = t.sitting_id
            WHERE s.child_id = ?
            ORDER BY t.id
            """,
            (child_id,),
        ).fetchall()
        grouped: dict[str, list[bool]] = {}
        for row in rows:
            grouped.setdefault(row["word_key"], []).append(bool(row["first_correct"]))
        return grouped

    def _used_keys(self, conn, sitting_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT word_key FROM turns WHERE sitting_id = ? ORDER BY id",
            (sitting_id,),
        ).fetchall()
        return [row["word_key"] for row in rows]

    def _deal(self, conn, sitting_row, just_key: str | None, now: datetime, rng) -> ChoiceWord | None:
        ends = load_dt(sitting_row["ends_at"])
        if ends is None or ends <= now:
            return None
        used_list = self._used_keys(conn, sitting_row["id"])
        used = set(used_list)
        if just_key:
            used.add(just_key)
            last = just_key
        else:
            last = used_list[-1] if used_list else None
        chosen = self._choose_for_child(conn, sitting_row["child_id"], used, last, rng)
        if chosen is None:
            return None
        conn.execute(
            "UPDATE sittings SET current_word_id = ?, pending_try = NULL WHERE id = ?",
            (chosen.id, sitting_row["id"]),
        )
        return chosen

    def _insert_turn(
        self,
        conn,
        sitting_row,
        word_row,
        first_try: str,
        first_correct: int,
        second_try: str | None,
        second_correct: int | None,
        now: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO turns (
                sitting_id, word_id, word_key, word_text, first_try, first_correct,
                second_try, second_correct, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sitting_row["id"],
                word_row["id"],
                word_row["word_key"],
                word_row["word"],
                first_try,
                first_correct,
                second_try,
                second_correct,
                dump_dt(now),
            ),
        )
        conn.execute(
            """
            UPDATE sittings
            SET correct_count = correct_count + ?,
                attempt_count = attempt_count + 1,
                pending_try = NULL
            WHERE id = ?
            """,
            (1 if first_correct else 0, sitting_row["id"]),
        )

    def _after_turn(self, conn, sitting_row, word_row, now: datetime, rng, message: str, pause_ms: int, tone: str) -> AnswerResult:
        ends = load_dt(sitting_row["ends_at"])
        time_up = ends is not None and ends <= now
        nxt = None if time_up else self._deal(conn, sitting_row, word_row["word_key"], now, rng)
        finished = time_up or nxt is None
        if finished:
            self._finish_row(conn, sitting_row["id"], now, force=True)
        return AnswerResult(
            kind="done" if finished else "next",
            token=sitting_row["token"],
            message=message,
            pause_ms=pause_ms,
            reveal_word=word_row["word"],
            reveal_sentence=word_row["sentence"],
            reveal_key=word_row["word_key"],
            reveal_tone=tone,
            next_word=nxt,
            remaining_seconds=0 if finished else _remaining(sitting_row, now),
            attempt_count=int(sitting_row["attempt_count"]) + 1,
            points=self._points(conn, sitting_row["id"]),
        )

    def _moved_on(self, conn, sitting_row, dealt: ChoiceWord, now: datetime) -> AnswerResult:
        return AnswerResult(
            kind="next",
            token=sitting_row["token"],
            message="Here is the next word.",
            pause_ms=0,
            next_word=dealt,
            remaining_seconds=_remaining(sitting_row, now),
            attempt_count=int(sitting_row["attempt_count"]),
            points=self._points(conn, sitting_row["id"]),
        )

    def _finish_row(self, conn, sitting_id: int, now: datetime, force: bool) -> None:
        row = conn.execute("SELECT * FROM sittings WHERE id = ?", (sitting_id,)).fetchone()
        if row is None or row["finished_at"]:
            return
        ends = load_dt(row["ends_at"])
        if not force and ends is not None and ends > now:
            return
        if row["pending_try"] and row["current_word_id"]:
            word = conn.execute("SELECT * FROM words WHERE id = ?", (row["current_word_id"],)).fetchone()
            if word is not None:
                self._insert_turn(conn, row, word, row["pending_try"], 0, None, None, now)
        conn.execute(
            """
            UPDATE sittings
            SET finished_at = ?, current_word_id = NULL, pending_try = NULL
            WHERE id = ?
            """,
            (dump_dt(now), sitting_id),
        )


def _checked_name(name: str) -> str:
    clean = display_name(name)
    if not valid_name(clean):
        raise StoreError("Use a name made of letters, up to 40 characters.")
    return clean


def _checked_list_name(name: str) -> str:
    clean = " ".join(name.split())
    if not 1 <= len(clean) <= 60:
        raise StoreError("Give the list a name, up to 60 characters.")
    return clean


def _child(row) -> Child:
    return Child(id=row["id"], name=row["name"], session_minutes=row["session_minutes"])


def _word(row) -> Word:
    return Word(
        id=row["id"],
        list_id=row["list_id"],
        word=row["word"],
        word_key=row["word_key"],
        sentence=row["sentence"],
        position=row["position"],
    )


def _sitting(row) -> Sitting:
    started = load_dt(row["started_at"])
    ends = load_dt(row["ends_at"])
    assert started is not None and ends is not None
    return Sitting(
        id=row["id"],
        token=row["token"],
        child_id=row["child_id"],
        started_at=started,
        ends_at=ends,
        finished_at=load_dt(row["finished_at"]),
        correct_count=row["correct_count"],
        attempt_count=row["attempt_count"],
        current_word_id=row["current_word_id"],
        pending_try=row["pending_try"],
    )


def _sum_points(rows) -> int:
    total = 0
    for row in rows:
        second = None if row["second_correct"] is None else bool(row["second_correct"])
        total += turn_points(bool(row["first_correct"]), second)
    return total


def _score(row, points: int) -> ScoreRow:
    started = load_dt(row["started_at"])
    assert started is not None
    return ScoreRow(
        id=row["id"],
        started_at=started,
        correct_count=row["correct_count"],
        attempt_count=row["attempt_count"],
        points=points,
    )


def _remaining(sitting_row, now: datetime) -> int:
    ends = load_dt(sitting_row["ends_at"])
    if ends is None:
        return 0
    return max(0, int((ends - now).total_seconds()))
