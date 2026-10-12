import random

from app.spelling import (
    ChoiceWord,
    bucket_for,
    choose_word,
    colour_attempt,
    normalise,
    parse_word_list,
    percent,
    sentence_parts,
    status_name,
    trend,
)


def test_normalise_ignores_case_and_curly_apostrophe():
    assert normalise("  They\u2019re. ") == "they're"
    assert normalise("THEY'RE") == "they're"
    assert normalise("theyre") == "theyre"


def test_blank_hides_the_target_and_not_a_longer_word():
    parts = sentence_parts("Therefore we stop there.", "there", reveal=False)
    assert parts == [
        {"kind": "text", "text": "Therefore we stop "},
        {"kind": "blank"},
        {"kind": "text", "text": "."},
    ]
    revealed = sentence_parts("They're going to the park.", "they're", reveal=True, tone="good")
    assert revealed[0] == {"kind": "word", "text": "They're", "tone": "good"}
    assert "they're" not in "".join(part.get("text", "") for part in sentence_parts("They're going to the park.", "they're", reveal=False)).casefold()


def test_homophones_do_not_blank_each_other():
    parts = sentence_parts("They're going to the park.", "their", reveal=False)
    assert parts == [{"kind": "text", "text": "They're going to the park."}]


def test_colour_hint_does_not_invent_letters_and_notes_a_missing_one():
    letters, note = colour_attempt("thier", "their")
    assert [letter.ok for letter in letters] == [True, True, False, False, True]
    assert note is None
    _letters, missing = colour_attempt("theyre", "they're")
    assert missing == "A letter is missing."
    _letters, extra = colour_attempt("theirr", "their")
    assert extra == "There is an extra letter."


def test_learned_needs_three_first_tries_and_a_miss_clears_it():
    assert status_name([]) == "not_started"
    assert status_name([True, True]) == "practising"
    assert status_name([False, True, True, True]) == "learned"
    assert status_name([True, True, True, False]) == "practising"
    assert bucket_for([True, False]) == "missed"


def test_choose_word_weights_and_does_not_repeat_early():
    words = [
        ChoiceWord(1, "missed", "missed", "The missed one."),
        ChoiceWord(2, "learned", "learned", "The learned one."),
    ]
    history = {"missed": [False], "learned": [True, True, True]}

    class Scripted(random.Random):
        def __init__(self, roll: float):
            super().__init__()
            self.roll = roll
            self.picks: list[int] = []

        def random(self) -> float:
            return self.roll

        def choice(self, seq):
            self.picks.append(0)
            return seq[0]

    assert choose_word(words, history, set(), None, Scripted(0.0)).word_key == "missed"
    assert choose_word(words, history, set(), None, Scripted(0.99)).word_key == "learned"
    only = [ChoiceWord(1, "only", "only", "Only one.")]
    assert choose_word(only, {}, {"only"}, "only", Scripted(0.0)).word_key == "only"
    pair = only + [ChoiceWord(2, "other", "other", "The other.")]
    assert choose_word(pair, {}, set(), "only", Scripted(0.0)).word_key == "other"


def test_percent_and_trend():
    assert percent(1, 2) == 50
    assert percent(1, 3) == 33
    assert percent(2, 3) == 67
    assert trend([80, 80]) == "early"
    assert trend([50, 50, 80, 80]) == "up"
    assert trend([80, 80, 50, 50]) == "down"
    assert trend([70, 72, 71, 73]) == "steady"


def test_parse_word_list_keeps_sentences_and_reports_every_bad_line():
    rows, errors = parse_word_list(
        "# comment\n"
        "they're | They're going to the park.\n"
        "their | Their coats are on the pegs.\n"
        "they're | They're late today.\n"
    )
    assert errors == []
    assert [(row.word, row.sentence) for row in rows] == [
        ("they're", "They're late today."),
        ("their", "Their coats are on the pegs."),
    ]
    _rows, errors = parse_word_list("no separator\ntheir |\n")
    assert len(errors) == 2
