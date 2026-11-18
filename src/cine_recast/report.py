"""Write the Korean report: summary, rights, swap table, per-shot scores, the user's confirmation of
each unit, sound, spending with the project budget, and open items."""

from pathlib import Path

from cine_recast import ledger, plan, sound, soundtrack
from cine_recast.analyze import load_shots, load_video
from cine_recast.casting import load as load_casting
from cine_recast.casting import not_applicable_holds
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
_SKIPPED = "(제외)"    # marks a score left out of the verdict
COPY_OVERLAP = 0.98     # a selected shot at this overlap_ratio or more is listed for a look (not a verdict)


def _num(value, digits: int = 3) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _NONE
    return f"{value:.{digits}f}"


def _judged(score: dict, name: str) -> str:
    """A judged score, followed by _SKIPPED when it was left out of the shot's verdict."""
    text = _num(score.get(name))
    skipped = score.get("skipped")
    if text != _NONE and isinstance(skipped, list) and name in skipped:
        text += f" {_SKIPPED}"
    return text


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
    """One line per rights item; an answer other than "yes" is marked as needing confirmation,
    except a likeness_consent "not_applicable" that casting.not_applicable_holds verifies."""
    rights = load_project(root).get("rights") or {}
    lines = ["## 권리 확인", ""]
    for item in RIGHTS_ITEMS:
        entry = rights.get(item)
        answer = entry.get("answer") if isinstance(entry, dict) else None
        note = entry.get("note") if isinstance(entry, dict) else ""
        line = f"- {_RIGHTS_LABELS[item]} ({item}): {answer or '미응답'}"
        if note:
            line += f" - {note}"
        if item == "likeness_consent" and answer == "not_applicable" and not_applicable_holds(root):
            line += " (도구 확인: 교체하는 인물이 없거나, 모두 참조 이미지 없이 이 프로젝트에서 생성한 가상 인물이다)"
        elif answer != "yes":
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


def _take_model(pp: ProjectPaths, item: dict, jobs: dict[str, dict]):
    """The model the unit's selected take was made with: the model its job was sent with (the job
    named in the take's meta.json), or the unit's model when that is not known."""
    take_id = item.get("selected_take")
    meta = pp.take_dir(item["id"], take_id) / "meta.json" if isinstance(take_id, str) and take_id else None
    if meta is not None and meta.is_file():
        try:
            job = jobs.get(read_project_json(pp.root, meta).get("job"))
        except (CineRecastError, AttributeError, TypeError):
            job = None
        sent = job.get("sent_params") if isinstance(job, dict) else None
        if isinstance(sent, dict) and isinstance(sent.get("model"), str):
            return sent["model"]
    return item.get("model")


def _shot_table(root: Path, units: list[dict]) -> tuple[list[str], list[int], list[int]]:
    """The per-shot table, the shots judged retry, and the shots whose picture is almost the
    reference's (overlap_ratio of COPY_OVERLAP or more), all from the selected takes. The model is
    the one the selected take was made with (_take_model)."""
    pp = ProjectPaths(root)
    jobs = {j["id"]: j for j in plan.load(root)["jobs"] if isinstance(j, dict) and isinstance(j.get("id"), str)}
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
               _take_model(pp, item, jobs) if item else None, item.get("selected_take") if item else None,
               *(_judged(score, name) for name in ("camera", "motion", "color", "flicker_ratio")),
               _num(score.get("overlap_ratio")), score.get("verdict"))
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    lines += ["", f"- 점수 뒤의 {_SKIPPED}는 판정에서 뺀 점수다. 교체가 그 점수를 당연히 바꾸기 때문에 판정에 넣지 않았다."]
    return [*lines, ""], retry, copies


def _cost(root: Path) -> list[str]:
    audit = ledger.audit(root)
    lines = ["## 비용", "", "| 묶음 | 한도 | 사용 | 잔액 |", "| --- | --- | --- | --- |"]
    for name, st in [*audit["batches"].items(), ("합계", audit["totals"])]:
        lines.append(f"| {_cell(name)} | {_num(st['limit'], 2)} | {_num(st['committed'], 2)} | {_num(st['remaining'], 2)} |")
    ids = ", ".join(_cell(pid) for pid in audit["provider_ids"]) or "없음"
    budget = audit["budget"]
    lines += ["", f"- 장부 점검: {'이상 없음' if audit['ok'] else '문제 있음'}"]
    lines += [f"  - {problem}" for problem in audit["problems"]]
    lines += ["- 프로젝트 예산: " + (
        f"{_num(budget['limit'], 2)} ({_cell(budget['by'])}), 승인한 묶음 한도 합계 {_num(budget['approved'], 2)}, "
        f"남은 예산 {_num(budget['remaining'], 2)}" if budget else "정하지 않음")]
    lines += [f"- 끝난 작업의 Higgsfield 작업 id: {ids}"]
    return [*lines, ""]


def _confirmations(units: list[dict]) -> tuple[list[str], list[str]]:
    """The table of who confirmed each unit's selected take, and the units without a confirmation."""
    lines = ["## 단위별 사용자 확인", "", "| 단위 | 숏 | 선택 테이크 | 확인 |", "| --- | --- | --- | --- |"]
    waiting = []
    for item in units:
        if plan.confirmed_take(item) is None:
            waiting.append(item["id"])
            who = "확인 안 함"
        else:
            confirmed = item["confirmed"]
            who = f"{confirmed['by']}, {confirmed['at']}" + (f" - {confirmed['note']}" if confirmed["note"] else "")
        lines.append("| " + " | ".join(_cell(v) for v in (
            item["id"], ", ".join(map(str, item.get("shots") or [])), item.get("selected_take"), who)) + " |")
    return [*lines, ""], waiting


def _sound(root: Path) -> tuple[list[str], list[str]]:
    """The sound section and its open items: sounds issued but not closed, and a soundtrack that
    no longer matches the placement in sounds.json (or its own record)."""
    pp = ProjectPaths(root)
    items = sound.load_items(root)
    lines = ["## 소리", ""]
    if not items:
        lines.append("소리 항목이 없다.")
    else:
        lines += ["| 소리 | 종류 | 이름 | 시작 프레임 | 길이(초) | 음량(dB) | 상태 |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for item in items:
            state = str(item.get("state")) + (", 음소거" if item.get("muted") is True else "")
            lines.append("| " + " | ".join(_cell(v) for v in (
                item.get("id"), item.get("kind"), item.get("label"), item.get("at_frame"), item.get("seconds"),
                item.get("gain_db"), state)) + " |")
    unclosed = [str(item.get("id")) for item in items if item.get("state") == "issued"]
    lines.append("")
    found = soundtrack.check(root, strict=False)
    if found is None and pp.soundtrack_file.is_file():
        drift = "out/soundtrack.wav가 믹스 기록과 다르다 - sound mix를 다시 한다"
        lines.append("- 사운드트랙: out/soundtrack.wav가 믹스 기록과 다르다")
    elif found is None:
        drift = "사운드트랙 없음"
        lines.append("- 사운드트랙: 아직 믹스하지 않았다")
    else:
        _, record = found
        drift = "있음 - sound mix를 다시 한다" if soundtrack.stale(root, record) else "없음"
        correlation = record.get("reference_correlation")
        lines.append(f"- 사운드트랙: out/soundtrack.wav, 음량 {_num(record.get('loudness_after'), 2)} LUFS, "
                     f"최고 레벨 {_num(record.get('peak_dbfs'), 2)} dBFS")
        lines.append("- 원본 소리와의 상관: " + ("원본에 소리 없음" if correlation is None else _num(correlation, 4)))
        cuts = [f"{entry.get('frame')}: {', '.join(entry.get('sounds') or []) or '없음'}"
                for entry in record.get("cuts") or [] if isinstance(entry, dict)]
        lines.append("- 컷마다 시작하는 소리: " + (", ".join(cuts) if cuts else "컷 없음"))
    open_lines = ["- 내보냈지만 기록을 닫지 않은 소리: " + (", ".join(unclosed) if unclosed else "없음"),
                  f"- 사운드트랙 뒤에 바뀐 소리 배치: {drift}"]
    return [*lines, ""], open_lines


def write_report(root: Path) -> Path:
    """Write out/report.md and return its path."""
    root = Path(root)
    pp = ProjectPaths(root)
    units = plan.units(root)
    shot_lines, retry, copies = _shot_table(root, units)
    confirm_lines, waiting = _confirmations(units)
    sound_lines, sound_open = _sound(root)
    unselected = [u["id"] for u in units if not u.get("selected_take")]
    open_lines = ["## 열린 항목", ""]
    open_lines.append("- 선택 테이크가 없는 단위: " + (", ".join(unselected) if unselected else "없음"))
    open_lines.append("- 사용자가 확인하지 않은 단위: " + (", ".join(waiting) if waiting else "없음"))
    open_lines.append("- 판정이 retry인 숏: " + (", ".join(map(str, retry)) if retry else "없음"))
    open_lines.append(f"- 화면이 원본과 거의 같은 숏(원본 유사 영역 {COPY_OVERLAP:.2f} 이상): "
                      + (", ".join(map(str, copies)) + " - 원본을 복사한 화면이 아닌지 눈으로 확인한다"
                         if copies else "없음"))
    open_lines += sound_open
    text = "\n".join(["# CineRecast 보고서", "", *_summary(root), *_rights(root), *_swap_table(root),
                      *shot_lines, *confirm_lines, *sound_lines, *_cost(root), *open_lines, ""])
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    out = pp.out_dir / "report.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    return out
