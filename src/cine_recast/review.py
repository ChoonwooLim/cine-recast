"""The user's look at each shot, and the record of the user's answer, before the film is assembled.

review_take writes into a conformed take's folder what the user looks at:

- review.mp4: the reference on the left and the conformed take on the right, the unit's shots in
  order, both the picture area only, the reference's frame rate and frame counts, no sound; each
  side is the picture area's size, scaled down to REVIEW_HEIGHT when it is taller.
- review.jpg: one row per shot: the first, middle and last frame of the reference, then the same
  three frames of the take, each cell CELL_WIDTH wide and labelled.

Like compare.mp4 they hold reference picture and are for looking at only; they never go into the
deliverable (jobs done, assets done, sound done and assemble --audio refuse files of takes/).

confirm records, on the unit in plan.json, that the user confirmed its selected take:
{"take", "by", "at", "note"}. Selecting another take, conforming the selected take again and
replacing the plan remove it (takes.select, conform.conform, plan.build), and assemble refuses a
unit without it.
"""

from pathlib import Path

from PIL import Image, ImageDraw

from cine_recast import plan
from cine_recast.analyze import _font, load_video
from cine_recast.conform import unit_shots
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import SCALE_FLAGS, iter_frames, probe, run
from cine_recast.jsonio import write_json
from cine_recast.lifecycle import is_text, now
from cine_recast.paths import ProjectPaths
from cine_recast.project import load_project
from cine_recast.takes import list_takes

REVIEW_HEIGHT = 540
CELL_WIDTH = 320


def _conformed(root: Path, item: dict, take_id: str) -> tuple[list, list[Path]]:
    """(the unit's shots, the take's conformed shot clips in the same order); refused unless the
    take exists and conform has written conform.json and every shot clip."""
    unit_id = item["id"]
    known = list_takes(root, unit_id)
    if take_id not in known:
        raise CineRecastError(f"unit {unit_id} has no take {take_id}; its takes: {', '.join(known) or 'none'}")
    folder = ProjectPaths(root).take_dir(unit_id, take_id)
    shots = unit_shots(root, item)
    clips = [folder / "shots" / f"{shot.index:04d}.mov" for shot in shots]
    if not (folder / "conform.json").is_file() or not all(clip.is_file() for clip in clips):
        raise CineRecastError(f"take {unit_id}/{take_id} has not been conformed; run conform first")
    return shots, clips


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _review_clip(reference: Path, item: dict, clips: list[Path], box, out: Path) -> None:
    height = min(box.h, REVIEW_HEIGHT)
    size = f"{_even(box.w * height / box.h)}:{_even(height)}"
    crop = f"crop={box.w}:{box.h}:{box.x}:{box.y}"
    side = f"{crop},scale={size}:{SCALE_FLAGS},setsar=1,format=yuv420p"
    joined = "".join(f"[{n}:v]" for n in range(1, len(clips) + 1))
    graph = (f"[0:v]trim=start_frame={item['start']}:end_frame={item['end']},setpts=PTS-STARTPTS,{side}[l];"
             f"{joined}concat=n={len(clips)}:v=1:a=0,{side}[r];[l][r]hstack=inputs=2[out]")
    args = ["-i", str(reference)]
    for clip in clips:
        args += ["-i", str(clip)]
    run([*args, "-filter_complex", graph, "-map", "[out]", "-an", "-c:v", "libx264", "-crf", "18", "-preset",
         "veryfast", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough", str(out)])
    got, want = probe(out).frames, item["end"] - item["start"]
    if got != want:
        out.unlink(missing_ok=True)
        raise CineRecastError(f"review.mp4 has {got} frames, the unit has {want}")


def _frames_at(path: Path, size: tuple[int, int], crop, wanted: list[int], start: int = 0) -> list[Image.Image]:
    """The frames wanted (numbers counted from `start` of the file) of the picture area at `size`."""
    first, last = min(wanted), max(wanted)
    found = {first + n: frame for n, frame in enumerate(
        iter_frames(path, size=size, crop=crop, start=start + first, count=last - first + 1))}
    missing = [n for n in wanted if n not in found]
    if missing:
        raise CineRecastError(f"could not read frames {missing} from {path}")
    return [Image.fromarray(found[n]) for n in wanted]


def _review_sheet(reference: Path, shots: list, clips: list[Path], box, out: Path) -> None:
    cell = (CELL_WIDTH, _even(CELL_WIDTH * box.h / box.w))
    font = _font()
    sheet = Image.new("RGB", (cell[0] * 6, cell[1] * len(shots)))
    draw = ImageDraw.Draw(sheet)
    for row, (shot, clip) in enumerate(zip(shots, clips)):
        offsets = [0, (shot.start + shot.end) // 2 - shot.start, shot.frames - 1]
        cells = [(img, f"{shot.index} ref {shot.start + n}") for img, n in
                 zip(_frames_at(reference, cell, box.as_crop(), offsets, start=shot.start), offsets)]
        cells += [(img, f"{shot.index} take {shot.start + n}") for img, n in
                  zip(_frames_at(clip, cell, box.as_crop(), offsets), offsets)]
        for column, (img, label) in enumerate(cells):
            x, y = column * cell[0], row * cell[1]
            sheet.paste(img, (x, y))
            bbox = draw.textbbox((x + 6, y + 6), label, font=font)
            draw.rectangle((bbox[0] - 3, bbox[1] - 3, bbox[2] + 3, bbox[3] + 3), fill=(0, 0, 0))
            draw.text((x + 6, y + 6), label, fill=(255, 255, 0), font=font)
    sheet.save(out, format="JPEG", quality=90)


def review_take(root: Path, unit_id: str, take_id: str) -> tuple[Path, Path]:
    """Write review.mp4 and review.jpg into the take folder (see the module docstring) and return
    their paths. Refused when the take is not conformed."""
    root = Path(root)
    item = plan.unit(root, unit_id)
    shots, clips = _conformed(root, item, take_id)
    _, box = load_video(root)
    reference = root / load_project(root)["reference"]
    folder = ProjectPaths(root).take_dir(unit_id, take_id)
    mp4, jpg = folder / "review.mp4", folder / "review.jpg"
    _review_clip(reference, item, clips, box, mp4)
    _review_sheet(reference, shots, clips, box, jpg)
    return mp4, jpg


def confirm(root: Path, unit_id: str, take_id: str, by: str, note: str = "") -> dict:
    """Record that the user (by) confirmed take_id for the unit and return the confirmation.
    Refused unless take_id is the unit's selected take and is conformed."""
    root = Path(root)
    if not is_text(by):
        raise CineRecastError("by must name the user who confirmed the take")
    if not isinstance(note, str):
        raise CineRecastError("note must be text")
    plan.unit(root, unit_id)                               # an unknown or damaged unit is refused here
    data = plan.load(root)
    item = next(u for u in data["units"] if isinstance(u, dict) and u.get("id") == unit_id)
    if item.get("selected_take") != take_id:
        raise CineRecastError(f"take {take_id} is not the selected take of unit {unit_id} (selected: "
                              f"{item.get('selected_take') or 'none'}); select it with takes select first")
    _conformed(root, item, take_id)
    confirmation = {"take": take_id, "by": by, "at": now(), "note": note}
    item["confirmed"] = confirmation
    write_json(ProjectPaths(root).plan_file, data)
    return confirmation
