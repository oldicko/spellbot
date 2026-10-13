"""Pure spelling rules: matching, hints, word choice, and scores.

The score of a sitting is the first try only. A second try can fix the
spelling on screen, and it does not change that score or the learned streak.
A word is learned after three correct first tries in a row. One miss later
puts it back to practising.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})
_EDGE_PUNCTUATION = ".,!?;:\"“”()[]"
_WORD_RE = re.compile(r"^[A-Za-z]+(?:[ '\u2019\u2018`´-][A-Za-z]+)*$")
_MAX_WORD = 40
_MAX_SENTENCE = 240
_MAX_PASTE = 100_000
_MAX_ROWS = 500

LEARNED_STREAK = 3
BUCKET_WEIGHTS = (
    ("missed", 6),
    ("not_started", 5),
    ("practising", 3),
    ("learned", 1),
)


@dataclass(frozen=True)
class ChoiceWord:
    id: int
    word_key: str
    word: str
    sentence: str


@dataclass(frozen=True)
class Letter:
    """One tile in a near-miss hint.

    A gap has an empty ch. The answer character that belongs there is not stored.
    """

    ch: str
    kind: str

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


@dataclass(frozen=True)
class ParsedWord:
    word: str
    word_key: str
    sentence: str


def normalise(value: str) -> str:
    text = value.strip().translate(_APOSTROPHES).casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip(_EDGE_PUNCTUATION)


def clean_attempt(value: str) -> str:
    text = value.replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def valid_word(word: str) -> bool:
    text = word.strip()
    if not 1 <= len(text) <= _MAX_WORD:
        return False
    return _WORD_RE.fullmatch(text) is not None


def valid_name(name: str) -> bool:
    text = " ".join(name.split())
    if not 1 <= len(text) <= 40:
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    return all(ch.isalpha() or ch in " '-’" for ch in text)


def display_name(name: str) -> str:
    return " ".join(name.split())


def streak_of(flags: Sequence[bool]) -> int:
    streak = 0
    for flag in reversed(flags):
        if not flag:
            break
        streak += 1
    return streak


def bucket_for(flags: Sequence[bool]) -> str:
    if not flags:
        return "not_started"
    if not flags[-1]:
        return "missed"
    if streak_of(flags) >= LEARNED_STREAK:
        return "learned"
    return "practising"


def status_name(flags: Sequence[bool]) -> str:
    bucket = bucket_for(flags)
    if bucket in {"learned", "not_started"}:
        return bucket
    return "practising"


def percent(correct: int, attempts: int) -> int:
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    return (100 * correct + attempts // 2) // attempts


def trend(scores: Sequence[int]) -> str:
    """Compare the newer half of the recent sittings with the older half."""
    window = list(scores[-10:])
    if len(window) < 4:
        return "early"
    half = len(window) // 2
    older = window[:-half]
    newer = window[-half:]
    delta = (sum(newer) / len(newer)) - (sum(older) / len(older))
    if delta >= 5:
        return "up"
    if delta <= -5:
        return "down"
    return "steady"


def word_pattern(word_key: str) -> re.Pattern[str]:
    pieces: list[str] = []
    for ch in word_key:
        if ch == "'":
            pieces.append("['’‘`´]")
        elif ch == " ":
            pieces.append(r"\s+")
        elif ch == "-":
            pieces.append("-")
        else:
            pieces.append(re.escape(ch))
    body = "".join(pieces)
    # Apostrophes are not word characters, so \b would split "they're".
    edge = r"[\w'’‘`´-]"
    return re.compile(rf"(?<!{edge}){body}(?!{edge})", re.IGNORECASE)


def sentence_parts(sentence: str, word_key: str, *, reveal: bool, tone: str = "show") -> list[dict[str, str]]:
    pattern = word_pattern(word_key)
    parts: list[dict[str, str]] = []
    last = 0
    for match in pattern.finditer(sentence):
        if match.start() > last:
            parts.append({"kind": "text", "text": sentence[last : match.start()]})
        if reveal:
            parts.append({"kind": "word", "text": match.group(0), "tone": tone})
        else:
            parts.append({"kind": "blank"})
        last = match.end()
    if last < len(sentence):
        parts.append({"kind": "text", "text": sentence[last:]})
    if not parts:
        parts = [{"kind": "text", "text": sentence}]
    return parts


def contains_word(sentence: str, word_key: str) -> bool:
    return word_pattern(word_key).search(sentence) is not None


# Tiles are drawn for one or two edits. A wilder guess would sketch the word.
_CLOSE_EDITS = 2


def colour_attempt(attempt: str, target_key: str) -> tuple[list[Letter], str | None]:
    """Line the attempt up with the answer.

    A missing letter becomes an empty tile in the hole, and letters after it
    stay correct. The missing character itself is never returned.
    """
    shown = normalise(attempt)
    letters, counts, missing = _align(shown, target_key)
    if sum(counts.values()) > _CLOSE_EDITS:
        return [], _length_note(len(target_key) - len(shown))
    return letters, _close_note(counts, missing)


def _length_note(gap: int) -> str | None:
    if gap == 1:
        return "A letter is missing."
    if gap > 1:
        return "Some letters are missing."
    if gap == -1:
        return "There is an extra letter."
    if gap < -1:
        return "There are extra letters."
    return None


def _close_note(counts: dict[str, int], missing: list[str]) -> str | None:
    used = [name for name in ("gap", "extra", "change", "swap") if counts[name]]
    if used == ["gap"]:
        if counts["gap"] == 1 and missing == ["'"]:
            return "The apostrophe is missing."
        if counts["gap"] == 1 and missing == ["-"]:
            return "The hyphen is missing."
        if counts["gap"] == 1:
            return "A letter is missing here."
        return "Some letters are missing."
    if used == ["extra"]:
        if counts["extra"] == 1:
            return "There is an extra letter."
        return "There are extra letters."
    if used == ["swap"] and counts["swap"] == 1:
        return "These two letters are the wrong way round."
    if used == ["change"] and counts["change"] == 1:
        return None
    if not used:
        return None
    return "Have a look at the marked letters."


def _align(shown: str, target: str) -> tuple[list[Letter], dict[str, int], list[str]]:
    """Damerau-Levenshtein alignment.

    One real edit outweighs the tie-break. The small extra cost prefers a
    later hole, so a doubled letter such as bok / book lines up as b o _ k.
    """
    n, m = len(target), len(shown)
    cap = n + m + 2
    unit = cap * cap
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    op: list[list[str | None]] = [[None] * (m + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + unit + (n - i + 1)
        op[i][0] = "gap"
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + unit + (m - j + 1)
        op[0][j] = "extra"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            choices: list[tuple[int, int, str]] = []
            if target[i - 1] == shown[j - 1]:
                choices.append((dp[i - 1][j - 1], 0, "ok"))
            else:
                choices.append((dp[i - 1][j - 1] + unit + (n - i + 1), 2, "change"))
            choices.append((dp[i - 1][j] + unit + (n - i + 1), 4, "gap"))
            choices.append((dp[i][j - 1] + unit + (m - j + 1), 3, "extra"))
            if (
                i > 1
                and j > 1
                and target[i - 1] == shown[j - 2]
                and target[i - 2] == shown[j - 1]
                and target[i - 1] != target[i - 2]
            ):
                choices.append((dp[i - 2][j - 2] + unit + (n - i + 1), 1, "swap"))
            _cost, _rank, kind = min(choices)
            dp[i][j] = _cost
            op[i][j] = kind

    letters: list[Letter] = []
    missing: list[str] = []
    counts = {"gap": 0, "extra": 0, "change": 0, "swap": 0}
    i, j = n, m
    while i or j:
        kind = op[i][j]
        if kind == "ok":
            letters.append(Letter(shown[j - 1], "ok"))
            i, j = i - 1, j - 1
        elif kind == "change":
            letters.append(Letter(shown[j - 1], "change"))
            counts["change"] += 1
            i, j = i - 1, j - 1
        elif kind == "gap":
            letters.append(Letter("", "gap"))
            missing.append(target[i - 1])
            counts["gap"] += 1
            i -= 1
        elif kind == "extra":
            letters.append(Letter(shown[j - 1], "extra"))
            counts["extra"] += 1
            j -= 1
        elif kind == "swap":
            letters.append(Letter(shown[j - 1], "swap"))
            letters.append(Letter(shown[j - 2], "swap"))
            counts["swap"] += 1
            i -= 2
            j -= 2
        else:
            raise RuntimeError("spelling alignment missed a cell")
    letters.reverse()
    missing.reverse()
    return letters, counts, missing


def choose_word(
    words: Sequence[ChoiceWord],
    history: Mapping[str, Sequence[bool]],
    used_keys: set[str],
    last_key: str | None,
    rng: random.Random,
) -> ChoiceWord | None:
    """Prefer missed and unseen words, and still revisit learned ones.

    Keys already used in this sitting wait until the others have had a turn.
    The word just finished is not repeated while another word is available.
    """
    by_key: dict[str, list[ChoiceWord]] = {}
    for word in words:
        by_key.setdefault(word.word_key, []).append(word)
    if not by_key:
        return None

    pool = set(by_key) - used_keys
    if not pool:
        pool = set(by_key)
    if last_key in pool and len(pool) > 1:
        pool.remove(last_key)

    buckets: dict[str, list[str]] = {name: [] for name, _weight in BUCKET_WEIGHTS}
    for key in sorted(pool):
        buckets[bucket_for(history.get(key, ()))].append(key)

    weighted = [(name, weight, buckets[name]) for name, weight in BUCKET_WEIGHTS if buckets[name]]
    total = sum(weight for _name, weight, _items in weighted)
    roll = rng.random() * total
    upto = 0.0
    chosen_keys = weighted[-1][2]
    for _name, weight, items in weighted:
        upto += weight
        if roll < upto:
            chosen_keys = items
            break

    key = rng.choice(chosen_keys)
    options = sorted(by_key[key], key=lambda item: item.id)
    return rng.choice(options)


def parse_word_list(text: str) -> tuple[list[ParsedWord], list[str]]:
    if len(text) > _MAX_PASTE:
        return [], ["That paste is too long. Split it into smaller lists."]

    errors: list[str] = []
    merged: dict[str, ParsedWord] = {}
    order: list[str] = []
    rows = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        rows += 1
        if rows > _MAX_ROWS:
            errors.append(f"Only the first {_MAX_ROWS} words can be added at once.")
            break
        if "|" in raw:
            word, sentence = raw.split("|", 1)
        elif "\t" in raw:
            word, sentence = raw.split("\t", 1)
        else:
            errors.append(f"Line {line_no} needs a word and a sentence separated by |.")
            continue
        word = " ".join(word.strip().split())
        sentence = " ".join(sentence.strip().split())
        if not word or not sentence:
            errors.append(f"Line {line_no} needs both a word and a sentence.")
            continue
        if not valid_word(word):
            errors.append(
                f"Line {line_no} has a word spellbot cannot use. Use letters, spaces, hyphens, and apostrophes."
            )
            continue
        if len(sentence) > _MAX_SENTENCE:
            errors.append(f"Line {line_no} has a sentence that is too long.")
            continue
        parsed = ParsedWord(word=word, word_key=normalise(word), sentence=sentence)
        if not parsed.word_key:
            errors.append(f"Line {line_no} has a word spellbot cannot use.")
            continue
        if parsed.word_key not in merged:
            order.append(parsed.word_key)
        merged[parsed.word_key] = parsed

    if len(errors) > 20:
        extra = len(errors) - 20
        errors = errors[:20] + [f"And {extra} more."]
    if errors:
        return [], errors
    return [merged[key] for key in order], []
