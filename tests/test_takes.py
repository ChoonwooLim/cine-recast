import pytest

from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json
from cine_recast.plan import unit
from cine_recast.takes import list_takes, new_take, select


def test_takes_are_never_overwritten(planned3, clip3):
    a = new_take(planned3.root, "u01", clip3, {"job": "x"}); b = new_take(planned3.root, "u01", clip3, {"job": "y"})
    assert (a, b) == ("t001", "t002") and list_takes(planned3.root, "u01") == ["t001", "t002"]
    select(planned3.root, "u01", "t002")
    assert unit(planned3.root, "u01")["selected_take"] == "t002"
    with pytest.raises(CineRecastError):
        select(planned3.root, "u01", "t009")


# --- extra guards beyond the brief's list ---

def test_take_folder_holds_raw_file_and_meta(planned3, clip3):
    t = new_take(planned3.root, "u01", clip3, {"job": "x"})
    folder = planned3.take_dir("u01", t)
    assert sorted(p.name for p in folder.iterdir()) == ["meta.json", "raw.mp4"]
    assert (folder / "raw.mp4").read_bytes() == clip3.read_bytes()
    assert read_json(folder / "meta.json") == {"job": "x"}


def test_numbering_continues_after_the_highest_take(planned3, clip3):
    (planned3.root / "takes" / "u01" / "t004").mkdir(parents=True)     # a gap left by hand
    assert new_take(planned3.root, "u01", clip3, {}) == "t005"
    assert list_takes(planned3.root, "u01") == ["t004", "t005"]
    assert list_takes(planned3.root, "u02") == []


@pytest.mark.parametrize("call", [
    lambda r, clip, tmp: new_take(r, "u09", clip, {}),                     # unknown unit
    lambda r, clip, tmp: new_take(r, "u01", tmp / "missing.mp4", {}),      # missing source file
    lambda r, clip, tmp: select(r, "u09", "t001"),                         # unknown unit
])
def test_take_refusals(planned3, clip3, tmp_path, call):
    with pytest.raises(CineRecastError):
        call(planned3.root, clip3, tmp_path)
    assert not (planned3.root / "takes").exists()
