"""Write the Korean report: summary, rights, swap table, per-shot scores, spending and open items."""

from pathlib import Path

from cine_recast import ledger, plan
from cine_recast.analyze import load_shots, load_video
from cine_recast.casting import load as load_casting
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json
from cine_recast.paths import ProjectPaths
from cine_recast.project import RIGHTS_ITEMS, load_project

_RIGHTS_LABELS = {
    "reference_rights": "원본 영상 권리",
    "likeness_consent": "인물 초상 동의",
    "background_rights": "배경 사용 권리",
}
_NONE = "-"


def _num(value, digits: int = 3) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _NONE
    return f"{value:.{digits}f}"


def _cell(value) -> str:
    text = _NONE if value in (None, "") else str(value)
    return text.replace("|", "/").replace("\n", " ")


def _fps_text(num: int, den: int) -> str:
    return f"{num // den}" if num % den == 0 else f"{num}/{den} ({num / den:.3f})"


def _summary(root: Path) -> list[str]:
    info, box = load_video(root)
    return [
        "## 요약", "",
        f"- 프로젝트 이름: {load_project(root).get('name', _NONE)}",
        f"- 숏 수: {len(load_shots(root))}",
        f"- 프레임 수: {info.frames}",
        f"- 프레임 속도: {_fps_text(info.fps_num, info.fps_den)} fps",
        f"- 화면 크기: {info.width}x{info.height}",
        f"- 화면 영역: x={box.x}, y={box.y}, 너비={box.w}, 높이={box.h}",
        "",
    ]


def _rights(root: Path) -> list[str]:
    rights = load_project(root).get("rights") or {}
    lines = ["## 권리 확인", ""]
    for item in RIGHTS_ITEMS:
        entry = rights.get(item)
        answer = entry.get("answer") if isinstance(entry, dict) else None
        note = entry.get("note") if isinstance(entry, dict) else ""
        line = f"- {_RIGHTS_LABELS[item]} ({item}): {answer or '미응답'}"
        if note:
            line += f" - {note}"
        if answer != "yes":
            line += " 확인 필요"
        lines.append(line)
    return [*lines, ""]


def _swap_table(root: Path) -> list[str]:
    lines = ["## 교체표", ""]
    try:
        data = load_casting(root)
    except CineRecastError:
        return [*lines, "casting.json이 없습니다.", ""]
    swaps = {s.get("element"): s for s in data.get("swaps") or [] if isinstance(s, dict)}
    lines += ["| 요소 | 종류 | 이름 | 숏 | 교체 이미지 | 메모 |", "| --- | --- | --- | --- | --- | --- |"]
    for element in data.get("elements") or []:
        swap = swaps.get(element.get("id"))
        images = ", ".join(Path(p).name for p in (swap or {}).get("images") or []) if swap else "교체 없음"
        shots = ", ".join(str(n) for n in element.get("shots") or [])
        lines.append("| " + " | ".join(_cell(v) for v in (
            element.get("id"), element.get("kind"), element.get("name"), shots,
            images or _NONE, (swap or {}).get("note"))) + " |")
    return [*lines, ""]


def _scores(pp: ProjectPaths, item: dict) -> dict:
    take_id = item.get("selected_take")
    path = pp.take_dir(item["id"], take_id) / "scores.json" if take_id else None
    if path is None or not path.is_file():
        return {}
    return (read_json(path).get("shots") or {})


def _shot_table(root: Path, units: list[dict]) -> tuple[list[str], list[int]]:
    pp = ProjectPaths(root)
    lines = ["## 숏별 결과", "",
             "| 숏 | 프레임 범위 | 단위 | 방법 | 모델 | 선택 테이크 | 카메라 | 움직임 | 색 | 깜빡임 비율 | 원본 유사 영역 | 판정 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    retry: list[int] = []
    scores = {item["id"]: _scores(pp, item) for item in units}
    for shot in load_shots(root):
        item = next((u for u in units if shot.index in (u.get("shots") or [])), None)
        score = scores[item["id"]].get(str(shot.index), {}) if item else {}
        if score.get("verdict") == "retry":
            retry.append(shot.index)
        row = (shot.index, f"{shot.start}-{shot.end}",
               item["id"] if item else None, item.get("method") if item else None,
               item.get("model") if item else None, item.get("selected_take") if item else None,
               _num(score.get("camera")), _num(score.get("motion")), _num(score.get("color")),
               _num(score.get("flicker_ratio")), _num(score.get("overlap_ratio")), score.get("verdict"))
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return [*lines, ""], retry


def _cost(root: Path) -> list[str]:
    audit = ledger.audit(root)
    lines = ["## 비용", "", "| 묶음 | 한도 | 사용 | 잔액 |", "| --- | --- | --- | --- |"]
    for name, st in audit["batches"].items():
        lines.append(f"| {_cell(name)} | {_num(st['limit'], 2)} | {_num(st['committed'], 2)} | {_num(st['remaining'], 2)} |")
    if not audit["batches"]:
        lines.append("| - | - | - | - |")
    lines += ["", f"- 장부 점검: {'이상 없음' if audit['ok'] else '문제 있음'}"]
    lines += [f"  - {problem}" for problem in audit["problems"]]
    return [*lines, ""]


def write_report(root: Path) -> Path:
    """Write out/report.md and return its path."""
    root = Path(root)
    pp = ProjectPaths(root)
    units = [u for u in plan.load(root)["units"] if isinstance(u, dict)]
    shot_lines, retry = _shot_table(root, units)
    unselected = [u["id"] for u in units if not u.get("selected_take")]
    open_lines = ["## 열린 항목", ""]
    open_lines.append("- 선택 테이크가 없는 단위: " + (", ".join(unselected) if unselected else "없음"))
    open_lines.append("- 판정이 retry인 숏: " + (", ".join(map(str, retry)) if retry else "없음"))
    text = "\n".join(["# CineRecast 보고서", "", *_summary(root), *_rights(root), *_swap_table(root),
                      *shot_lines, *_cost(root), *open_lines, ""])
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    out = pp.out_dir / "report.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out
