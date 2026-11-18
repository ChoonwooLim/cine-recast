from cine_recast.textrules import han_chars, is_english_prompt


def test_han_chars_reports_only_han():
    assert han_chars("abc \ud55c\uae00 \u4e2d\u6587 \u4e2d") == ["\u4e2d", "\u6587"]


def test_han_ranges_cover_bopomofo_strokes_enclosed_ideographs_and_extensions_g_h():
    for ch in ("\u3105", "\u31a0", "\u31c0", "\u3220", "\u3247", "\u3280", "\u32b0", "\u32c0", "\u3358",
               "\u337b", "\u33e0", "\U0001f200", "\U00030000", "\U000323af"):
        assert han_chars(ch) == [ch], hex(ord(ch))


def test_enclosed_hangul_stays_allowed():
    for ch in ("\u3200", "\u321e", "\u3260", "\u327f"):
        assert han_chars(ch) == [], hex(ord(ch))


def test_is_english_prompt():
    assert is_english_prompt("A wide aerial shot, 24 fps.")
    assert not is_english_prompt("")
    assert not is_english_prompt("wide shot \ud55c\uae00")      # Hangul
    assert not is_english_prompt("wide shot \u4e2d")            # Han
    assert not is_english_prompt("wide shot \u30ab")            # Kana
