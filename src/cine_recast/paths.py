import hashlib
import os
from collections.abc import Iterable
from pathlib import Path


def inside_folder(path: Path, folders: Iterable[Path]) -> Path | None:
    """The first of `folders` that `path` is or lies inside, or None.

    Both sides are resolved first (symbolic links and ".." are followed, so another spelling of a
    project folder is still that folder) and compared case-insensitively on Windows.
    """
    def comparable(item: Path) -> Path:
        return Path(os.path.normcase(Path(item).resolve()))

    target = comparable(path)
    for folder in folders:
        base = comparable(folder)
        if target == base or base in target.parents:
            return Path(folder)
    return None


def sha256_of(path: Path) -> str:
    """SHA-256 of the file's bytes, streamed, as hex."""
    return _sha256(path)


def _sha256(path: Path) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def same_bytes(path: Path, other: Path) -> bool:
    """True when both files exist and hold the same bytes: sizes first, then a streamed SHA-256."""
    path, other = Path(path), Path(other)
    if not (path.is_file() and other.is_file()) or path.stat().st_size != other.stat().st_size:
        return False
    return _sha256(path) == _sha256(other)


class ProjectPaths:
    """Layout of a project folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def project_file(self) -> Path:
        return self.root / "project.json"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def video_file(self) -> Path:
        return self.analysis_dir / "video.json"

    @property
    def cuts_file(self) -> Path:
        return self.analysis_dir / "cuts.json"

    @property
    def audio_file(self) -> Path:
        return self.analysis_dir / "audio.json"

    @property
    def review_dir(self) -> Path:
        return self.analysis_dir / "review"

    def shot_dir(self, index: int) -> Path:
        return self.analysis_dir / "shots" / f"{index:04d}"

    def sheet_file(self, index: int) -> Path:
        return self.shot_dir(index) / "sheet.json"

    def unit_dir(self, unit_id: str) -> Path:
        return self.analysis_dir / "units" / unit_id

    @property
    def casting_file(self) -> Path:
        return self.root / "casting.json"

    @property
    def models_file(self) -> Path:
        return self.root / "models.json"

    @property
    def plan_file(self) -> Path:
        return self.root / "plan.json"

    @property
    def ledger_file(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def ledger_lock(self) -> Path:
        return self.root / "ledger.lock"

    @property
    def assets_file(self) -> Path:
        return self.root / "assets.json"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    def asset_dir(self, element: str) -> Path:
        return self.assets_dir / element

    @property
    def takes_dir(self) -> Path:
        return self.root / "takes"

    def take_dir(self, unit_id: str, take_id: str) -> Path:
        return self.takes_dir / unit_id / take_id

    @property
    def sounds_file(self) -> Path:
        return self.root / "sounds.json"

    @property
    def sounds_dir(self) -> Path:
        return self.root / "sounds"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def soundtrack_file(self) -> Path:
        return self.out_dir / "soundtrack.wav"

    @property
    def soundtrack_record(self) -> Path:
        return self.out_dir / "soundtrack.json"

    def material_folders(self) -> list[Path]:
        """The folders that hold the reference, what the tool made from it, and the stored results:
        a file inside one of them is never a new generation result."""
        return [self.reference_dir, self.analysis_dir, self.takes_dir, self.out_dir, self.assets_dir]
