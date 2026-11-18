from cine_recast.errors import CineRecastError

_HAN_RANGES = (
    (0x2E80, 0x2FDF),
    (0x3005, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303B),
    (0x3100, 0x312F),       # Bopomofo
    (0x31A0, 0x31BF),       # Bopomofo extended
    (0x31C0, 0x31EF),       # CJK strokes
    (0x3220, 0x3247),       # parenthesized and circled ideographs (0x3200-0x321E and 0x3260-0x327F are Hangul)
    (0x3280, 0x32B0),
    (0x32C0, 0x32CB),       # ideographic telegraph symbols for months
    (0x3358, 0x3370),       # ideographic telegraph symbols for hours
    (0x337B, 0x337F),       # era names
    (0x33E0, 0x33FE),       # ideographic telegraph symbols for days
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x1F200, 0x1F2FF),     # enclosed ideographic supplement
    (0x20000, 0x2FA1F),
    (0x30000, 0x323AF),     # extensions G and H
)
_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xAC00, 0xD7A3),
)
_KANA_RANGES = ((0x3040, 0x30FF),)


def _in(ch: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in ranges)


def han_chars(text: str) -> list[str]:
    """Han characters found in text, unique, in order of first appearance."""
    found: list[str] = []
    for ch in text:
        if _in(ch, _HAN_RANGES) and ch not in found:
            found.append(ch)
    return found


def han_problem(name: str, value) -> str | None:
    """The refusal sentence for a free text that holds Han characters, or None. `name` names the
    text as the user gave it (a flag such as --note, or a key). Every free text a command writes into
    a project file (batch names, --by, --note, --reason, --provider-id, labels, the project name, the
    file names of input images) goes through this check, so the rule is the same everywhere."""
    found = han_chars(value) if isinstance(value, str) else []
    return f"{name} must not contain Han characters ({''.join(found)})" if found else None


def refuse_han(name: str, value) -> None:
    """Raise CineRecastError with han_problem's sentence when `value` holds Han characters."""
    problem = han_problem(name, value)
    if problem:
        raise CineRecastError(problem)


def is_english_prompt(text: str) -> bool:
    """False for empty text or text containing Han, Hangul or Kana characters."""
    if not text.strip():
        return False
    return not any(
        _in(ch, _HAN_RANGES) or _in(ch, _HANGUL_RANGES) or _in(ch, _KANA_RANGES)
        for ch in text
    )
