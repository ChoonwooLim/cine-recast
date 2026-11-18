"""Run the analysis of a project's reference video: picture area, cuts, one folder per shot."""

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from cine_recast.cuts import Shot, detect_cuts, shots_from_cuts
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import VideoInfo, cut_frames, extract_still, iter_frames, probe
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.measure import measure_clip
from cine_recast.paths import ProjectPaths
from cine_recast.picture import PictureBox, detect_picture_box
from cine_recast.project import load_project

CELL_WIDTH = 320
SHOT_STRIP_FRAMES = 8


def _reference(root: Path) -> Path:
    return Path(root) / load_project(root)["reference"]


_VIDEO_KEYS = {"width": int, "height": int, "fps_num": int, "fps_den": int, "frames": int,
               "duration": (int, float), "has_audio": bool, "pix_fmt": str, "picture_box": dict}


def load_video(root: Path) -> tuple[VideoInfo, PictureBox]:
    pp = ProjectPaths(Path(root))
    if not pp.video_file.is_file():
        raise CineRecastError("analysis has not been run (analysis/video.json missing)")
    data = read_project_json(pp.root, pp.video_file, _VIDEO_KEYS)
    for key in ("x", "y", "w", "h"):
        value = data["picture_box"].get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise CineRecastError(f"analysis/video.json: the key 'picture_box.{key}' must be a whole number")
    box = PictureBox.from_dict(data["picture_box"])
    info = VideoInfo(
        width=data["width"], height=data["height"], fps_num=data["fps_num"], fps_den=data["fps_den"],
        frames=data["frames"], duration=float(data["duration"]), has_audio=data["has_audio"], pix_fmt=data["pix_fmt"],
    )
    return info, box


def _load_cuts(root: Path) -> list[int]:
    pp = ProjectPaths(Path(root))
    if not pp.cuts_file.is_file():
        raise CineRecastError("analysis has not been run (analysis/cuts.json missing)")
    cuts = read_project_json(pp.root, pp.cuts_file, {"cuts": list})["cuts"]
    if not all(isinstance(cut, int) and not isinstance(cut, bool) for cut in cuts):
        raise CineRecastError("analysis/cuts.json: the key 'cuts' must be a list of whole frame numbers")
    return cuts


def load_shots(root: Path) -> list[Shot]:
    info, _ = load_video(root)
    return shots_from_cuts(_load_cuts(root), info.frames)


def analyze(root: Path, *, cuts: list[int] | None = None) -> dict:
    """Write analysis/video.json, cuts.json and one folder per shot (clip, stills, measured sheet).

    Re-running replaces the shots.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    load_project(root)
    if pp.plan_file.exists():
        raise CineRecastError("plan exists; analysis is frozen")
    ref = _reference(root)
    info = probe(ref)
    box = detect_picture_box(ref, info)
    if cuts is None:
        cut_list, source = detect_cuts(ref, info, box), "detected"
    else:
        cut_list, source = list(cuts), "manual"
    shots = shots_from_cuts(cut_list, info.frames)

    write_json(pp.video_file, {
        "width": info.width, "height": info.height, "fps_num": info.fps_num, "fps_den": info.fps_den,
        "frames": info.frames, "duration": info.duration, "has_audio": info.has_audio,
        "pix_fmt": info.pix_fmt, "picture_box": box.to_dict(),
    })
    write_json(pp.cuts_file, {"cuts": cut_list, "source": source})

    shots_dir = pp.analysis_dir / "shots"
    if shots_dir.exists():
        shutil.rmtree(shots_dir)
    crop = box.as_crop()
    for shot in shots:
        folder = pp.shot_dir(shot.index)
        cut_frames(ref, shot.start, shot.end, folder / "ref.mp4", crop=crop)
        stills = {"first": shot.start, "mid": (shot.start + shot.end) // 2, "last": shot.end - 1}
        for label, frame in stills.items():
            extract_still(ref, frame, folder / "stills" / f"{label}.png", crop=crop)
        measures = measure_clip(folder / "ref.mp4")
        write_json(pp.sheet_file(shot.index), {
            "schema": 1, "shot": shot.index, "start": shot.start, "end": shot.end,
            "frames": shot.frames, "picture_box": box.to_dict(),
            "stills": {label: f"stills/{label}.png" for label in stills},
            "camera": measures["camera"], "color": measures["color"], "flicker": measures["flicker"],
            "elements": [], "pose": None, "depth": None, "mattes": None,
            "audio_hits": [], "dialogue": [],
        })
    return {"shots": len(shots), "cuts": cut_list, "picture_box": box.to_dict()}


def _font() -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=20)
    except TypeError:
        return ImageFont.load_default()


def _grab(ref: Path, box: PictureBox, indices: list[int], size: tuple[int, int]) -> list[Image.Image]:
    """Decode the picture area at `size` for the wanted frame numbers (repeats allowed)."""
    first, last = min(indices), max(indices)
    frames = {first + i: f for i, f in enumerate(
        iter_frames(ref, size=size, crop=box.as_crop(), start=first, count=last - first + 1))}
    missing = [i for i in indices if i not in frames]
    if missing:
        raise CineRecastError(f"could not read frames {missing} from {ref}")
    return [Image.fromarray(frames[i]) for i in indices]


def _strip(images: list[Image.Image], labels: list[int], out: Path) -> None:
    font = _font()
    w, h = images[0].size
    sheet = Image.new("RGB", (w * len(images), h))
    draw = ImageDraw.Draw(sheet)
    for n, (img, label) in enumerate(zip(images, labels)):
        sheet.paste(img, (n * w, 0))
        text = str(label)
        bbox = draw.textbbox((n * w + 6, 6), text, font=font)
        draw.rectangle((bbox[0] - 3, bbox[1] - 3, bbox[2] + 3, bbox[3] + 3), fill=(0, 0, 0))
        draw.text((n * w + 6, 6), text, fill=(255, 255, 0), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def review_strips(root: Path) -> list[Path]:
    """Write analysis/review images: four frames around each cut, eight frames across each shot."""
    root = Path(root)
    pp = ProjectPaths(root)
    info, box = load_video(root)
    shots = load_shots(root)
    cut_list = _load_cuts(root)
    ref = _reference(root)
    size = (CELL_WIDTH, max(2, round(CELL_WIDTH * box.h / box.w / 2) * 2))
    written: list[Path] = []
    for c in cut_list:
        labels = [c - 2, c - 1, c, c + 1]
        wanted = [min(max(i, 0), info.frames - 1) for i in labels]
        out = pp.review_dir / f"cut_{c:04d}.png"
        _strip(_grab(ref, box, wanted, size), labels, out)
        written.append(out)
    for shot in shots:
        n = min(SHOT_STRIP_FRAMES, shot.frames)
        wanted = [shot.start + int((i + 0.5) * shot.frames / n) for i in range(n)]
        out = pp.review_dir / f"shot_{shot.index:04d}.png"
        _strip(_grab(ref, box, wanted, size), wanted, out)
        written.append(out)
    return written
