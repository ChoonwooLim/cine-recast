_HAN_RANGES = (
    (0x2E80, 0x2FDF),
    (0x3005, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303B),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
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


def is_english_prompt(text: str) -> bool:
    """False for empty text or text containing Han, Hangul or Kana characters."""
    if not text.strip():
        return False
    return not any(
        _in(ch, _HAN_RANGES) or _in(ch, _HANGUL_RANGES) or _in(ch, _KANA_RANGES)
        for ch in text
    )
