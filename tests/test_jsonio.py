from cine_recast.jsonio import append_jsonl, read_json, read_jsonl, write_json


def test_write_json_roundtrip_keeps_korean(tmp_path):
    p = tmp_path / "a" / "b.json"
    write_json(p, {"name": "\ud55c\uae00"})
    assert "\ud55c\uae00" in p.read_text(encoding="utf-8")
    assert read_json(p) == {"name": "\ud55c\uae00"}


def test_jsonl_append_and_missing(tmp_path):
    p = tmp_path / "l.jsonl"
    assert read_jsonl(p) == []
    append_jsonl(p, {"a": 1})
    append_jsonl(p, {"a": 2})
    assert read_jsonl(p) == [{"a": 1}, {"a": 2}]
