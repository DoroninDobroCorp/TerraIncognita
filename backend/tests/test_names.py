"""Tests for script- and accent-insensitive place-name matching."""

from __future__ import annotations

import pytest

from app.services.names import (
    distinctive_tokens,
    fold,
    is_generic_name,
    name_similarity,
    shares_type_word,
    token_similarity,
    tokens,
)


@pytest.mark.parametrize(("raw", "folded"), [
    ("Tvrđava Mamula", "tvrdjava mamula"),
    ("Luštica", "lustica"),
    ("Форт Мамула", "fort mamula"),
    ("Київ", "kiyiv"),
    ("Łódź", "lodz"),
    ("Straße", "strasse"),
    ("ÉCOLE", "ecole"),
])
def test_fold(raw, folded):
    assert fold(raw) == folded


def test_tokens_split_on_punctuation_and_keep_unicode_words():
    assert tokens("Fort-Mamula (Island), 1853!") == ["fort", "mamula", "island", "1853"]
    assert tokens("城堡 Castle") == ["城堡", "castle"]
    assert tokens(None) == [] and tokens("") == []


@pytest.mark.parametrize(("name", "distinctive"), [
    ("Fort Mamula", {"mamula"}),
    ("Mamula Fort", {"mamula"}),
    ("Tvrđava Mamula", {"mamula"}),
    ("Old Bunker", set()),
    ("The Bunker Complex", set()),
    ("Abandoned building", set()),
    ("Bunker 7", {"7"}),
    ("St. Nicholas Church", {"nicholas"}),
    ("Заброшенный завод", {"zabroshenniy"}),
    ("Yugoslav Bunkers", {"yugoslav"}),   # plurals of type words are still type words
    ("Churches of Kotor", {"kotor"}),
    ("X", set()),  # single letters carry no identity
    (None, set()),
])
def test_distinctive_tokens(name, distinctive):
    got = distinctive_tokens(name)
    if name == "Заброшенный завод":  # transliteration detail is not the point
        assert len(got) == 1
    else:
        assert got == distinctive


@pytest.mark.parametrize(("name", "generic"), [
    ("Bunker", True), ("Old Bunker", True), ("Disused railway station", True),
    ("Bunker Zlatica", False), ("Bunker 3", False), (None, True), ("", True),
])
def test_is_generic_name(name, generic):
    assert is_generic_name(name) is generic


@pytest.mark.parametrize(("a", "b", "at_least", "at_most"), [
    ("Fort Mamula", "Mamula Fort", 1.0, 1.0),
    ("Fort Mamula", "Tvrđava Mamula", 1.0, 1.0),
    ("Fort Mamula", "Форт Мамула", 1.0, 1.0),
    ("Mamula", "Fort Mamula Island", 1.0, 1.0),     # containment is a full match
    ("Luštica", "Lustica", 1.0, 1.0),
    ("Mamula", "Mamulla", 1.0, 1.0),                 # typo-tolerant for 4+ letter tokens
    ("Šetalište Pet Danica", "Шеталиште Пет Даница", 0.5, 1.0),  # Serbian Latin vs Cyrillic
    ("Fort Arza", "Fort Mamula", 0.0, 0.0),
    ("Bunker 1", "Bunker 2", 0.0, 0.0),              # different numbers never match
    ("Bunker Zlatica 1", "Bunker Zlatica 2", 0.0, 0.0),
    ("Bunker 3", "Bunker 3", 1.0, 1.0),
    ("Old Bunker", "The Bunker Complex", 0.0, 0.0),  # generic names do not identify anything
    (None, "Fort Arza", 0.0, 0.0),
    ("Fort Arza", "", 0.0, 0.0),
    ("Arza Fort", "Arza Hotel", 1.0, 1.0),
])
def test_name_similarity(a, b, at_least, at_most):
    assert at_least <= name_similarity(a, b) <= at_most


def test_token_similarity_is_symmetric():
    a, b = frozenset({"arza", "lustica"}), frozenset({"arza"})
    assert token_similarity(a, b) == token_similarity(b, a) == 1.0
    assert token_similarity(frozenset({"alfa", "beta"}), frozenset({"beta", "gamma"})) == 0.5


def test_short_tokens_must_match_exactly():
    assert token_similarity(frozenset({"abc"}), frozenset({"abd"})) == 0.0


def test_shares_type_word():
    assert shares_type_word("Old Bunker", "The Bunker Complex")
    assert not shares_type_word("Old Bunker", "The Mill")
    assert not shares_type_word("The Mill", "The Church")


@pytest.mark.parametrize(("raw", "words"), [
    ("हिन्दी भाषा", 2),  # Devanagari: vowel signs and the virama are combining marks
    ("กรุงเทพ", 1),  # Thai
    ("Fort-Mamula 2", 3),
])
def test_tokens_do_not_shred_scripts_with_combining_marks(raw, words):
    out = tokens(raw)
    assert len(out) == words
    assert all(len(t) > 1 for t in out if not t.isdigit())


def test_the_same_text_folds_alike_composed_or_decomposed():
    import unicodedata

    for raw in ("Йосип", "Київ Їжак", "Ёлка", "Škoda Čačak", "Ѓуро Ќосе"):
        assert fold(unicodedata.normalize("NFC", raw)) == fold(unicodedata.normalize("NFD", raw)), raw


def test_macedonian_letters_are_transliterated_not_left_in_cyrillic():
    assert fold("Ѓорѓи") == "gjorgji"
    assert name_similarity("Ѓуро", "Gjuro") == 1.0
    assert not any("\u0400" <= ch <= "\u04ff" for ch in fold("Ѓорѓи Ќосе Ѕвезда"))


def test_fold_is_idempotent():
    for raw in ("Мост Ѓорѓи", "Київ", "Straße", "Ünïcödé", "Łódź", "ශ්\u200dරී ලංකා", "Fort Mamula"):
        assert fold(fold(raw)) == fold(raw), raw


def test_joiners_do_not_split_a_word():
    out = tokens("ශ්\u200dරී ලංකා")
    assert len(out) == 2 and all(len(t) > 1 for t in out)
    assert tokens("\u200dAbc") == ["abc"] and tokens("Abc\u200d") == ["abc"]


def test_cyrillic_letters_that_only_exist_as_base_plus_accent_are_transliterated():
    """Decomposition frees the base letter ("ѐ" is "е" + grave); it must be transliterated after the accent is dropped."""
    assert fold("Ѐ") == "e" and fold("ӣ") == "i"
    assert not any("Ѐ" <= ch <= "ӿ" for ch in fold("ѐӣӑӓё"))
