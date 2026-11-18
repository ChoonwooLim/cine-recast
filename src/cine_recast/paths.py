from pathlib import Path


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
    def assets_file(self) -> Path:
        return self.root / "assets.json"

    def asset_dir(self, element: str) -> Path:
        return self.root / "assets" / element

    def take_dir(self, unit_id: str, take_id: str) -> Path:
        return self.root / "takes" / unit_id / take_id

    @property
    def out_dir(self) -> Path:
        return self.root / "out"
