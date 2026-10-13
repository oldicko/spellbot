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


def _tiles(letters):
    return [(letter.ch, letter.kind) for letter in letters]


def test_missing_letter_is_a_hole_and_the_tail_stays_correct():
    letters, note = colour_attempt("Therpy", "therapy")
    assert _tiles(letters) == [
        ("t", "ok"),
        ("h", "ok"),
        ("e", "ok"),
        ("r", "ok"),
        ("", "gap"),
        ("p", "ok"),
        ("y", "ok"),
    ]
    assert note == "A letter is missing here."
    assert all(letter.ch != "a" for letter in letters)


def test_extra_letter_is_marked_and_the_tail_stays_correct():
    letters, note = colour_attempt("therappy", "therapy")
    assert _tiles(letters) == [
        ("t", "ok"),
        ("h", "ok"),
        ("e", "ok"),
        ("r", "ok"),
        ("a", "ok"),
        ("p", "ok"),
        ("p", "extra"),
        ("y", "ok"),
    ]
    assert note == "There is an extra letter."
    letters, several = colour_attempt("therapyyy", "therapy")
    assert [letter.kind for letter in letters][-3:] == ["ok", "extra", "extra"]
    assert several == "There are extra letters."


def test_one_different_letter_is_the_only_mark():
    letters, note = colour_attempt("therapi", "therapy")
    assert _tiles(letters) == [
        ("t", "ok"),
        ("h", "ok"),
        ("e", "ok"),
        ("r", "ok"),
        ("a", "ok"),
        ("p", "ok"),
        ("i", "change"),
    ]
    assert note is None


def test_adjacent_letters_the_wrong_way_round_are_one_swap():
    letters, note = colour_attempt("thier", "their")
    assert _tiles(letters) == [
        ("t", "ok"),
        ("h", "ok"),
        ("i", "swap"),
        ("e", "swap"),
        ("r", "ok"),
    ]
    assert note == "These two letters are the wrong way round."
    swapped, swap_note = colour_attempt("threapy", "therapy")
    assert _tiles(swapped) == [
        ("t", "ok"),
        ("h", "ok"),
        ("r", "swap"),
        ("e", "swap"),
        ("a", "ok"),
        ("p", "ok"),
        ("y", "ok"),
    ]
    assert swap_note == "These two letters are the wrong way round."


def test_missing_apostrophe_and_hyphen_are_named_and_not_shown():
    letters, note = colour_attempt("theyre", "they're")
    assert _tiles(letters) == [
        ("t", "ok"),
        ("h", "ok"),
        ("e", "ok"),
        ("y", "ok"),
        ("", "gap"),
        ("r", "ok"),
        ("e", "ok"),
    ]
    assert note == "The apostrophe is missing."
    assert all(letter.ch != "'" for letter in letters)
    hyphen, hyphen_note = colour_attempt("icecream", "ice-cream")
    assert ("", "gap") in _tiles(hyphen)
    assert hyphen_note == "The hyphen is missing."
    assert all(letter.ch != "-" for letter in hyphen)


def test_doubled_letter_hole_follows_the_typed_letters():
    letters, note = colour_attempt("bok", "book")
    assert _tiles(letters) == [("b", "ok"), ("o", "ok"), ("", "gap"), ("k", "ok")]
    assert note == "A letter is missing here."
    assert all(letter.ch != "o" or letter.kind == "ok" for letter in letters)


def test_two_holes_and_a_mix_stay_on_the_row():
    letters, note = colour_attempt("thrpy", "therapy")
    assert [letter.kind for letter in letters] == ["ok", "ok", "gap", "ok", "gap", "ok", "ok"]
    assert note == "Some letters are missing."
    mixed, mixed_note = colour_attempt("therpi", "therapy")
    assert _tiles(mixed) == [
        ("t", "ok"),
        ("h", "ok"),
        ("e", "ok"),
        ("r", "ok"),
        ("", "gap"),
        ("p", "ok"),
        ("i", "change"),
    ]
    assert mixed_note == "Have a look at the marked letters."
    assert all(letter.ch != "a" and letter.ch != "y" for letter in mixed)


def test_a_far_guess_has_no_tiles_and_does_not_spell_the_word():
    letters, note = colour_attempt("xxxxx", "quartz")
    assert letters == []
    assert note == "A letter is missing."
    shorter, short_note = colour_attempt("thpy", "therapy")
    assert shorter == []
    assert short_note == "Some letters are missing."
    longer, long_note = colour_attempt("therapyyyy", "therapy")
    assert longer == []
    assert long_note == "There are extra letters."


def test_exact_spelling_is_all_green():
    letters, note = colour_attempt("therapy", "therapy")
    assert note is None
    assert [letter.kind for letter in letters] == ["ok"] * 7


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
