from cine_recast.textrules import han_chars, is_english_prompt


def test_han_chars_reports_only_han():
    assert han_chars("abc \ud55c\uae00 \u4e2d\u6587 \u4e2d") == ["\u4e2d", "\u6587"]


def test_is_english_prompt():
    assert is_english_prompt("A wide aerial shot, 24 fps.")
    assert not is_english_prompt("")
    assert not is_english_prompt("wide shot \ud55c\uae00")      # Hangul
    assert not is_english_prompt("wide shot \u4e2d")            # Han
    assert not is_english_prompt("wide shot \u30ab")            # Kana
