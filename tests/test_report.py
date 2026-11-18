from conftest import generate_and_use, png, write_models

from cine_recast.assemble import assemble
from cine_recast.jsonio import read_json, write_json
from cine_recast.plan import build
from cine_recast.report import write_report
from cine_recast.textrules import han_chars


def _open_items(text: str) -> list[str]:
    return text.split("## \uc5f4\ub9b0 \ud56d\ubaa9")[1].strip().splitlines()


def test_report_lists_shots_almost_identical_to_the_reference(finished3):
    scores_file = finished3.take_dir("u01", "t001") / "scores.json"
    scores = read_json(scores_file)
    for shot, overlap in (("1", 0.99), ("2", 0.98), ("3", 0.979)):
        scores["shots"][shot]["overlap_ratio"] = overlap
    write_json(scores_file, scores)
    [line] = [l for l in _open_items(write_report(finished3.root).read_text(encoding="utf-8")) if "0.98" in l]
    assert ": 1, 2 " in line and "3" not in line.split(":")[1]
    for shot in scores["shots"].values():
        shot["overlap_ratio"] = 0.5
    write_json(scores_file, scores)
    [line] = [l for l in _open_items(write_report(finished3.root).read_text(encoding="utf-8")) if "0.98" in l]
    assert line.endswith(": \uc5c6\uc74c")


def _section(text: str, heading: str) -> str:
    return text.split(heading)[1].split("\n## ")[0]


def test_report_totals_costs_and_lists_provider_ids(finished3):
    from cine_recast import ledger

    ledger.approve(finished3.root, "c", 20.0, "owner"); ledger.issue(finished3.root, "j0900", "c", 5.0)
    cost = _section(write_report(finished3.root).read_text(encoding="utf-8"), "## \ube44\uc6a9")
    assert "| \ud569\uacc4 | 70.00 | 43.00 | 27.00 |" in cost                     # b: 50 / 38, c: 20 / 5
    [ids] = [line for line in cost.splitlines() if "Higgsfield" in line]
    assert ids.endswith(": prov-1")


def _rights_line(root, item: str) -> str:
    text = write_report(root).read_text(encoding="utf-8")
    return next(line for line in _section(text, "## \uad8c\ub9ac \ud655\uc778").splitlines() if f"({item})" in line)


def test_verified_not_applicable_consent_needs_no_confirmation(assets3, tmp_path):
    root = assets3.root                                               # likeness_consent is "not_applicable"
    generate_and_use(root, "hero", tmp_path)                          # a person generated without references
    write_models(root)
    build(root, model="seedance_2_5", method="edit", batch="b")
    assert "\ud655\uc778 \ud544\uc694" not in _rights_line(root, "likeness_consent")
    c = read_json(assets3.casting_file); c["swaps"][0]["images"] = [png(tmp_path / "photo.png").as_posix()]
    write_json(assets3.casting_file, c)                               # an outside photo: no longer verified
    assert "\ud655\uc778 \ud544\uc694" in _rights_line(root, "likeness_consent")


def test_report_content(finished3):
    assemble(finished3.root)
    text = write_report(finished3.root).read_text(encoding="utf-8")
    assert text.startswith("# CineRecast \ubcf4\uace0\uc11c")
    for heading in ("## \uc694\uc57d", "## \uad8c\ub9ac \ud655\uc778", "## \uad50\uccb4\ud45c", "## \uc20f\ubcc4 \uacb0\uacfc", "## \ube44\uc6a9", "## \uc5f4\ub9b0 \ud56d\ubaa9"):
        assert heading in text
    assert "| 1 | 0-30 | u01 |" in text and "t001" in text and han_chars(text) == []


# --- task 20: sound (F), budget (H) and the user's confirmations (J) in the report ---

def test_report_sound_section_and_open_items(finished3, tmp_path):
    from conftest import AUDIO_MODEL, EXAMPLE_MODEL, done_sound, make_tone

    from cine_recast import ledger, sound, soundtrack

    root = finished3.root
    write_json(finished3.models_file, {"models": [EXAMPLE_MODEL, AUDIO_MODEL]})
    text = write_report(root).read_text(encoding="utf-8")
    assert "\uc18c\ub9ac \ud56d\ubaa9\uc774 \uc5c6\ub2e4." in _section(text, "## \uc18c\ub9ac")
    done_sound(root, make_tone(tmp_path / "door.wav", 2), seconds=2, at_frame=30, gain_db=-3)
    item = sound.add(root, kind="music", label="bed", prompt="Calm low strings.", model="audio_model_x", seconds=4,
                     batch="snd-2", gain_db=-12)
    sound.set_cost(root, item["id"], 2.0); ledger.approve(root, "snd-2", 2.0, "owner"); sound.issue_next(root, "snd-2")
    record = soundtrack.mix(root)
    text = write_report(root).read_text(encoding="utf-8")
    part = _section(text, "## \uc18c\ub9ac")
    assert "| s0001 | sfx | test sound | 30 | 2 | -3.0 | done |" in part
    assert "| s0002 | music | bed | 0 | 4 | -12.0 | issued |" in part
    assert f"\uc74c\ub7c9 {record['loudness_after']:.2f} LUFS" in part and f"\ucd5c\uace0 \ub808\ubca8 {record['peak_dbfs']:.2f} dBFS" in part
    assert "\uc6d0\ubcf8 \uc18c\ub9ac\uc640\uc758 \uc0c1\uad00: \uc6d0\ubcf8\uc5d0 \uc18c\ub9ac \uc5c6\uc74c" in part
    assert "- \ucef7\ub9c8\ub2e4 \uc2dc\uc791\ud558\ub294 \uc18c\ub9ac: 30: s0001, 54: \uc5c6\uc74c" in part
    items = _open_items(text)
    assert "- \ub0b4\ubcf4\ub0c8\uc9c0\ub9cc \uae30\ub85d\uc744 \ub2eb\uc9c0 \uc54a\uc740 \uc18c\ub9ac: s0002" in items
    assert "- \uc0ac\uc6b4\ub4dc\ud2b8\ub799 \ub4a4\uc5d0 \ubc14\ub010 \uc18c\ub9ac \ubc30\uce58: \uc5c6\uc74c" in items
    sound.set_placement(root, "s0001", at_frame=40, mute=True)
    text = write_report(root).read_text(encoding="utf-8")
    assert "| s0001 | sfx | test sound | 40 | 2 | -3.0 | done, \uc74c\uc18c\uac70 |" in _section(text, "## \uc18c\ub9ac")
    assert "- \uc0ac\uc6b4\ub4dc\ud2b8\ub799 \ub4a4\uc5d0 \ubc14\ub010 \uc18c\ub9ac \ubc30\uce58: \uc788\uc74c - sound mix\ub97c \ub2e4\uc2dc \ud55c\ub2e4" in _open_items(text)
    finished3.soundtrack_file.write_bytes(b"changed")
    assert ("- \uc0ac\uc6b4\ub4dc\ud2b8\ub799 \ub4a4\uc5d0 \ubc14\ub010 \uc18c\ub9ac \ubc30\uce58: out/soundtrack.wav\uac00 \ubbf9\uc2a4 \uae30\ub85d\uacfc \ub2e4\ub974\ub2e4 - sound mix\ub97c \ub2e4\uc2dc \ud55c\ub2e4"
            in _open_items(write_report(root).read_text(encoding="utf-8")))


def test_report_shows_the_budget(finished3):
    from cine_recast import ledger

    cost = _section(write_report(finished3.root).read_text(encoding="utf-8"), "## \ube44\uc6a9")
    assert "- \ud504\ub85c\uc81d\ud2b8 \uc608\uc0b0: \uc815\ud558\uc9c0 \uc54a\uc74c" in cost
    ledger.set_budget(finished3.root, 200.0, "owner")
    cost = _section(write_report(finished3.root).read_text(encoding="utf-8"), "## \ube44\uc6a9")
    assert "- \ud504\ub85c\uc81d\ud2b8 \uc608\uc0b0: 200.00 (owner), \uc2b9\uc778\ud55c \ubb36\uc74c \ud55c\ub3c4 \ud569\uacc4 50.00, \ub0a8\uc740 \uc608\uc0b0 150.00" in cost


def test_report_shows_who_confirmed_each_unit(finished3):
    text = write_report(finished3.root).read_text(encoding="utf-8")
    at = read_json(finished3.plan_file)["units"][0]["confirmed"]["at"]
    assert f"| u01 | 1, 2, 3 | t001 | owner, {at} |" in _section(text, "## \ub2e8\uc704\ubcc4 \uc0ac\uc6a9\uc790 \ud655\uc778")
    assert "- \uc0ac\uc6a9\uc790\uac00 \ud655\uc778\ud558\uc9c0 \uc54a\uc740 \ub2e8\uc704: \uc5c6\uc74c" in _open_items(text)
    data = read_json(finished3.plan_file); del data["units"][0]["confirmed"]; write_json(finished3.plan_file, data)
    text = write_report(finished3.root).read_text(encoding="utf-8")
    assert "| u01 | 1, 2, 3 | t001 | \ud655\uc778 \uc548 \ud568 |" in _section(text, "## \ub2e8\uc704\ubcc4 \uc0ac\uc6a9\uc790 \ud655\uc778")
    assert "- \uc0ac\uc6a9\uc790\uac00 \ud655\uc778\ud558\uc9c0 \uc54a\uc740 \ub2e8\uc704: u01" in _open_items(text)


def test_a_pair_sheet_made_from_a_person_is_not_verified_as_not_applicable(assets3, tmp_path):
    from cine_recast import assets, ledger
    from cine_recast.casting import check, not_applicable_holds
    from cine_recast.project import set_right

    root = assets3.root
    hero = generate_and_use(root, "hero", tmp_path)                       # virtual, made without references
    set_right(root, "likeness_consent", "yes")
    pair = assets.add(root, element="sky_battle", prompt="The rider from the reference image under a sunset sky.",
                      model="image_model_x", batch="pair", refs=[root / hero])
    assets.set_cost(root, pair["id"], 1.0); ledger.approve(root, "pair", 1.0, "owner"); assets.issue_next(root, "pair")
    stored = assets.record_done(root, pair["id"], "prov-pair", 1.0, [png(tmp_path / "pair.png")])["files"][0]
    assets.use(root, "sky_battle", stored)
    set_right(root, "likeness_consent", "not_applicable")
    assert check(root) == [] and not_applicable_holds(root) is False
    write_models(root)
    build(root, model="seedance_2_5", method="edit", batch="b")
    assert "\ud655\uc778 \ud544\uc694" in _rights_line(root, "likeness_consent")
