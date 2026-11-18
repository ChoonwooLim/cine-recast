"""Join the selected, conformed takes into the finished film and make the side-by-side comparison.

The finished film is built only from conformed takes plus an audio file the caller supplies; an
audio file that is the reference's material is refused. The reference video is used by compare()
alone, and only there is its audio heard.
"""

import tempfile
from fractions import Fraction
from pathlib import Path

from cine_recast import plan
from cine_recast.analyze import load_shots, load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import SCALE_FLAGS, probe, run
from cine_recast.paths import ProjectPaths, inside_folder, same_bytes
from cine_recast.project import load_project

COMPARE_HEIGHT = 540


def _clips_by_shot(root: Path) -> dict[int, Path]:
    """The conformed clip of every shot, taken from each unit's selected take."""
    pp = ProjectPaths(root)
    clips: dict[int, Path] = {}
    for item in plan.units(root):
        unit_id = item["id"]
        take_id = item.get("selected_take")
        if not take_id:
            raise CineRecastError(f"unit {unit_id} has no selected take; select one before assembling")
        for index in item.get("shots") or []:
            clip = pp.take_dir(unit_id, take_id) / "shots" / f"{index:04d}.mov"
            if not clip.is_file():
                raise CineRecastError(
                    f"unit {unit_id}: conformed clip for shot {index} is missing: {clip}; "
                    f"conform take {take_id} first")
            clips[index] = clip
    return clips


def _check_audio(root: Path, audio: Path) -> None:
    """Refuse an audio file that may carry the reference's sound: a file inside the project's
    reference/, analysis/, takes/ (a take from an edit model can keep it) or out/ (compare.mp4 has it)
    folder, or a file whose bytes equal the reference (the original file given to init)."""
    if not audio.is_file():
        raise CineRecastError(f"audio file not found: {audio}")
    pp = ProjectPaths(root)
    folder = inside_folder(audio, [pp.reference_dir, pp.analysis_dir, pp.takes_dir, pp.out_dir])
    if folder is not None:
        raise CineRecastError(f"audio file {audio} lies inside the project's {folder.name} folder, which can hold the "
                              "sound of the reference; the film's audio must be a file made for the film")
    if same_bytes(audio, root / load_project(root)["reference"]):
        raise CineRecastError(f"audio file {audio} is a copy of the reference video; the film's audio must be a file "
                              "made for the film, the sound of the reference is never used")


def _concat_entry(path: Path) -> str:
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"


def assemble(root: Path, *, audio: Path | None = None) -> Path:
    """Write out/master.mov (ProRes, stream copy) and out/final.mp4 and return the mp4's path."""
    root = Path(root)
    pp = ProjectPaths(root)
    info, _ = load_video(root)
    clips = _clips_by_shot(root)
    shots = load_shots(root)
    missing = [shot.index for shot in shots if shot.index not in clips]
    if missing:
        raise CineRecastError(f"no unit covers shot(s) {', '.join(map(str, missing))}; plan every shot first")
    if audio is not None:
        _check_audio(root, Path(audio))
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    master, final = pp.out_dir / "master.mov", pp.out_dir / "final.mp4"
    with tempfile.TemporaryDirectory() as scratch:
        listing = Path(scratch) / "concat.txt"
        listing.write_text("".join(_concat_entry(clips[shot.index]) for shot in shots), encoding="utf-8")
        run(["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v:0", "-c", "copy", "-an", str(master)])
    got = probe(master).frames
    if got != info.frames:
        raise CineRecastError(f"assembled master has {got} frames, the reference has {info.frames}")
    args = ["-i", str(master)]
    if audio is not None:
        args += ["-i", str(audio)]
    args += ["-map", "0:v:0"]
    if audio is not None:
        seconds = Fraction(info.frames * info.fps_den, info.fps_num)
        args += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-t", f"{float(seconds):.6f}"]
    else:
        args += ["-an"]
    run([*args, "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
          "-fps_mode", "passthrough", "-movflags", "+faststart", str(final)])
    return final


def compare(root: Path) -> Path:
    """Write out/compare.mp4: the reference on the left, out/final.mp4 on the right, both 540 high."""
    root = Path(root)
    pp = ProjectPaths(root)
    final = pp.out_dir / "final.mp4"
    if not final.is_file():
        raise CineRecastError(f"{final} not found; run assemble first")
    reference = root / load_project(root)["reference"]
    info = probe(reference)
    scale = f"scale=-2:{COMPARE_HEIGHT}:{SCALE_FLAGS},setsar=1"
    graph = f"[0:v]{scale}[l];[1:v]{scale}[r];[l][r]hstack=inputs=2[out]"
    args = ["-i", str(reference), "-i", str(final), "-filter_complex", graph, "-map", "[out]"]
    if info.has_audio:
        seconds = Fraction(info.frames * info.fps_den, info.fps_num)
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-t", f"{float(seconds):.6f}"]
    else:
        args += ["-an"]
    out = pp.out_dir / "compare.mp4"
    run([*args, "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
         "-fps_mode", "passthrough", "-movflags", "+faststart", str(out)])
    return out
