"""Write the Korean report: summary, rights, swap table, per-shot scores, spending and open items."""

from pathlib import Path

from cine_recast import ledger, plan
from cine_recast.analyze import load_shots, load_video
from cine_recast.casting import load as load_casting
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_project_json
from cine_recast.paths import ProjectPaths
from cine_recast.project import RIGHTS_ITEMS, load_project

_RIGHTS_LABELS = {
    "reference_rights": "원본 영상 권리",
    "likeness_consent": "인물 초상 동의",
    "background_rights": "배경 사용 권리",
}
_NONE = "-"
COPY_OVERLAP = 0.98     # a selected shot at this overlap_ratio or more is listed for a look (not a verdict)


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
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list) or not isinstance(data.get("swaps"), list):
        return [*lines, "casting.json의 형식이 맞지 않습니다.", ""]
    swaps = {s.get("element"): s for s in data["swaps"] if isinstance(s, dict) and isinstance(s.get("element"), str)}
    lines += ["| 요소 | 종류 | 이름 | 숏 | 교체 이미지 | 메모 |", "| --- | --- | --- | --- | --- | --- |"]
    for element in (e for e in data["elements"] if isinstance(e, dict)):
        swap = swaps.get(element.get("id")) if isinstance(element.get("id"), str) else None
        images = ", ".join(Path(p).name for p in (swap or {}).get("images") or [] if isinstance(p, str)) \
            if swap else "교체 없음"
        shots = ", ".join(str(n) for n in element.get("shots") or []) if isinstance(element.get("shots"), list) else ""
        lines.append("| " + " | ".join(_cell(v) for v in (
            element.get("id"), element.get("kind"), element.get("name"), shots,
            images or _NONE, (swap or {}).get("note"))) + " |")
    return [*lines, ""]


def _scores(pp: ProjectPaths, item: dict) -> dict:
    take_id = item.get("selected_take")
    path = pp.take_dir(item["id"], take_id) / "scores.json" if isinstance(take_id, str) and take_id else None
    if path is None or not path.is_file():
        return {}
    shots = read_project_json(pp.root, path, {}).get("shots")
    return shots if isinstance(shots, dict) else {}


def _shot_table(root: Path, units: list[dict]) -> tuple[list[str], list[int], list[int]]:
    """The per-shot table, the shots judged retry, and the shots whose picture is almost the
    reference's (overlap_ratio of COPY_OVERLAP or more), all from the selected takes."""
    pp = ProjectPaths(root)
    lines = ["## 숏별 결과", "",
             "| 숏 | 프레임 범위 | 단위 | 방법 | 모델 | 선택 테이크 | 카메라 | 움직임 | 색 | 깜빡임 비율 | 원본 유사 영역 | 판정 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    retry: list[int] = []
    copies: list[int] = []
    scores = {item["id"]: _scores(pp, item) for item in units}
    for shot in load_shots(root):
        item = next((u for u in units if shot.index in (u.get("shots") or [])), None)
        score = scores[item["id"]].get(str(shot.index), {}) if item else {}
        if score.get("verdict") == "retry":
            retry.append(shot.index)
        overlap = score.get("overlap_ratio")
        if isinstance(overlap, (int, float)) and not isinstance(overlap, bool) and overlap >= COPY_OVERLAP:
            copies.append(shot.index)
        row = (shot.index, f"{shot.start}-{shot.end}",
               item["id"] if item else None, item.get("method") if item else None,
               item.get("model") if item else None, item.get("selected_take") if item else None,
               _num(score.get("camera")), _num(score.get("motion")), _num(score.get("color")),
               _num(score.get("flicker_ratio")), _num(score.get("overlap_ratio")), score.get("verdict"))
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return [*lines, ""], retry, copies


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
    units = plan.units(root)
    shot_lines, retry, copies = _shot_table(root, units)
    unselected = [u["id"] for u in units if not u.get("selected_take")]
    open_lines = ["## 열린 항목", ""]
    open_lines.append("- 선택 테이크가 없는 단위: " + (", ".join(unselected) if unselected else "없음"))
    open_lines.append("- 판정이 retry인 숏: " + (", ".join(map(str, retry)) if retry else "없음"))
    open_lines.append(f"- 화면이 원본과 거의 같은 숏(원본 유사 영역 {COPY_OVERLAP:.2f} 이상): "
                      + (", ".join(map(str, copies)) + " - 원본을 복사한 화면이 아닌지 눈으로 확인한다"
                         if copies else "없음"))
    text = "\n".join(["# CineRecast 보고서", "", *_summary(root), *_rights(root), *_swap_table(root),
                      *shot_lines, *_cost(root), *open_lines, ""])
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    out = pp.out_dir / "report.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out
