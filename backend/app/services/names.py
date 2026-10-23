"""Place-name normalisation and fuzzy matching (script- and accent-insensitive).

Used by fusion (is this the same place?), scoring/diversity (is this the same site?) and
enrichment (which Wikipedia article is about this place?). Pure functions, no I/O.

"Fort Mamula", "Mamula Fort", "Tvrđava Mamula" and "Форт Мамула" all reduce to the same
distinctive token, ``mamula``; "Old Bunker" / "Bunker" reduce to nothing (a *generic* name).
"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

STRONG_MATCH = 0.75  # token overlap at which two names count as "the same name"
WEAK_MATCH = 0.40  # …and at which they at least overlap

# Cyrillic → ASCII. Deliberately simple; fuzzy token matching absorbs scheme differences
# (Serbian "ш" → "sh" vs Latin "š" → "s" still match at ratio > 0.8).
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "ђ": "dj", "е": "e", "ё": "e",
    "є": "ye", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y", "ј": "j", "к": "k",
    "л": "l", "љ": "lj", "м": "m", "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "ћ": "c", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "џ": "dz", "ш": "sh",
    "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ѝ": "i", "ў": "u",
    "ѓ": "gj", "ќ": "kj", "ѕ": "dz",
}
_LATIN_SPECIAL = {"đ": "dj", "ł": "l", "ø": "o", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i", "ð": "d",
                  "þ": "th"}

# Words that say *what kind* of thing a place is (in a handful of languages), plus articles and
# vague adjectives. A name made only of these ("Old Bunker") does not identify a specific place.
_GENERIC_WORDS = frozenset("""
the a an of de da di del della la le el los las der die das den von van na pod
st saint sveti sveta sveto sv san santa santo
old new former ex great big small little upper lower stari stara staro novi nova
abandoned disused ruined ruins ruin derelict
fort fortress fortification forte fortezza fortaleza festung tvrdjava tvrdava twierdza krepost
castle castello kastel chateau schloss burg zamek zamok
bunker bunkr bunkar schron
church chapel crkva kapela chiesa iglesia kirche kosciol cerkov tserkov tserkva
monastery manastir kloster
museum muzej muzeum museo
tower kula tunnel tunel cave pecina spilja jama mine adit shaft
lighthouse fenjer svetionik
building house kuca factory fabrika zavod plant mill station stanica stazione railway pruga
bridge most hotel restaurant cafe bar pub
memorial monument spomenik pamyatnik
park garden viewpoint lookout island otok ostrvo complex kompleks site area base barracks
kasarna military vojni vojna object objekat entrance shaft
""".split())


def _transliterate(text: str) -> str:
    return "".join(_CYRILLIC[ch] if ch in _CYRILLIC else _LATIN_SPECIAL.get(ch, ch) for ch in text)


def fold(text: str) -> str:
    """Lower-case, strip accents, transliterate Cyrillic to ASCII.

    Composed first (the same text typed composed or decomposed folds alike: "й" is one letter, not "и" plus
    a mark), transliterated, decomposed to drop accents, and transliterated once more for the base letters
    that decomposition frees (Macedonian "ѓ" is "г" plus an accent).
    """
    composed = unicodedata.normalize("NFC", text).lower()
    decomposed = unicodedata.normalize("NFKD", _transliterate(composed))
    return _transliterate("".join(c for c in decomposed if not unicodedata.combining(c)))


def tokens(name: str | None) -> list[str]:
    """All folded alphanumeric tokens of a name."""
    if not name:
        return []
    # Letters, digits and combining marks: vowel signs and viragas of Indic/Thai/Burmese scripts are
    # category M, and splitting at them (as a plain \w+ does) shreds a word into single letters.
    out: list[str] = []
    word: list[str] = []
    for ch in fold(name):
        if ch in "\u200c\u200d" and word:
            continue  # joiners sit inside a word (Sinhala "ශ්\u200dරී", Malayalam chillus): they do not end it
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            word.append(ch)
        elif word:
            out.append("".join(word))
            word = []
    if word:
        out.append("".join(word))
    return out


def _is_generic_word(token: str) -> bool:
    if token in _GENERIC_WORDS:
        return True
    # plurals: "bunkers", "churches"
    return (token.endswith("s") and token[:-1] in _GENERIC_WORDS) or (
        token.endswith("es") and token[:-2] in _GENERIC_WORDS
    )


def distinctive_tokens(name: str | None) -> frozenset[str]:
    """Tokens that identify the place (numbers included), without type words and articles."""
    return frozenset(
        t for t in tokens(name) if t.isdigit() or (len(t) > 1 and not _is_generic_word(t))
    )


def is_generic_name(name: str | None) -> bool:
    """True when the name carries no identifying token ("Bunker", "Old fort", "Abandoned building")."""
    return not distinctive_tokens(name)


def _token_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or min(len(a), len(b)) < 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def token_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Overlap of two distinctive-token sets in [0, 1]; 0 if either is empty or numbers differ.

    Overlap coefficient (|A∩B| / min(|A|,|B|)), so "Arza" vs "Fort Arza Luštica" is a full match.
    Two names that carry *different* numbers ("Bunker 1" / "Bunker 2") never match.
    """
    if not a or not b:
        return 0.0
    nums_a = {t for t in a if t.isdigit()}
    nums_b = {t for t in b if t.isdigit()}
    if nums_a and nums_b and nums_a != nums_b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    matched = sum(1 for t in small if any(_token_match(t, u) for u in large))
    return matched / len(small)


def name_similarity(a: str | None, b: str | None) -> float:
    """Similarity of two place names in [0, 1] (0 when either is generic)."""
    return token_similarity(distinctive_tokens(a), distinctive_tokens(b))


def shares_type_word(a: str | None, b: str | None) -> bool:
    """Whether two (possibly generic) names share any word at all ("Old Bunker" / "The Bunker Complex")."""
    ta, tb = set(tokens(a)), set(tokens(b))
    return bool((ta & tb) - {"the", "a", "an", "of"})
