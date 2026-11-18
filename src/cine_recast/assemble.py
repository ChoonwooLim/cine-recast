"""Join the selected, conformed takes into the finished film and make the side-by-side comparison.

The finished film is built only from conformed takes the user confirmed, plus generated sound: the
soundtrack sound mix wrote (used only while its bytes are the ones mixed) or an audio file the
caller supplies (an audio file that is the reference's material is refused). The reference video
is used by compare() alone, and only there is its audio heard.

The sound is cut or padded to the film's length: master.mov carries it as 24-bit PCM, final.mp4 as
AAC at 256 kb/s. A preview (out/preview.mp4) joins the selected takes without the confirmations so
that the shots can be watched in sequence before they are confirmed. Extra shots (extras.py, shots
that are not in the reference) are never used: the film is the reference's shots, one for one.
"""

import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from cine_recast import plan, soundtrack
from cine_recast.analyze import load_shots, load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import SCALE_FLAGS, probe, run
from cine_recast.paths import ProjectPaths, inside_folder, same_bytes
from cine_recast.project import load_project

COMPARE_HEIGHT = 540
SOUND_RATE = 48000
_H264 = ["-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough"]


@dataclass(frozen=True)
class Assembled:
    path: Path      # out/final.mp4, or out/preview.mp4 for a preview
    sound: str      # which sound went in: "soundtrack (3 items)", "file <name>" or "none"


def _clips_by_shot(root: Path, units: list[dict]) -> dict[int, Path]:
    """The conformed clip of every shot, taken from each unit's selected take."""
    pp = ProjectPaths(root)
    clips: dict[int, Path] = {}
    for item in units:
        unit_id = item["id"]
        take_id = item.get("selected_take")
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


def _sound(root: Path, *, audio: Path | None, silent: bool, preview: bool) -> tuple[Path | None, str]:
    """(the audio file to put under the picture or None, the description assemble prints)."""
    if silent:
        return None, "none"
    if audio is not None:
        _check_audio(root, Path(audio))
        return Path(audio), f"file {Path(audio).name}"
    found = soundtrack.check(root, strict=not preview)
    if found is None:
        return None, "none"
    path, record = found
    return path, f"soundtrack ({len(record['sounds'])} items)"


def _unconfirmed(units: list[dict]) -> list[str]:
    return [item["id"] for item in units if plan.confirmed_take(item) is None]


def assemble(root: Path, *, audio: Path | None = None, silent: bool = False, preview: bool = False) -> Assembled:
    """Write out/master.mov (ProRes, stream copy) and out/final.mp4, or with preview only
    out/preview.mp4, and return the written film's path and the sound that went in.

    The sound is, in this order: none with silent; the audio file given; out/soundtrack.wav when it
    exists (refused when it is not the file sound mix wrote; a preview leaves such a file out); none.
    Every unit needs a selected take, conformed and, except for a preview, confirmed by the user.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    if silent and audio is not None:
        raise CineRecastError("--silent makes a film without sound, so it cannot be combined with --audio")
    if preview and audio is not None:
        raise CineRecastError("--preview takes the soundtrack when one is mixed, so it cannot be combined with --audio")
    info, _ = load_video(root)
    units = plan.units(root)
    for item in units:
        if not item.get("selected_take"):
            raise CineRecastError(f"unit {item['id']} has no selected take; select one before assembling")
    if not preview:
        waiting = _unconfirmed(units)
        if waiting:
            raise CineRecastError(
                f"the user has not confirmed the selected take of unit(s) {', '.join(waiting)}; show each shot with "
                "takes review and record the user's answer with takes confirm (assemble --preview joins the "
                "shots without confirmations, to watch them in sequence)")
    clips = _clips_by_shot(root, units)
    shots = load_shots(root)
    missing = [shot.index for shot in shots if shot.index not in clips]
    if missing:
        raise CineRecastError(f"no unit covers shot(s) {', '.join(map(str, missing))}; plan every shot first")
    sound_file, described = _sound(root, audio=audio, silent=silent, preview=preview)
    samples = round(Fraction(info.frames * info.fps_den * SOUND_RATE, info.fps_num))
    fit = ["-af", f"aresample={SOUND_RATE},apad=whole_len={samples},atrim=end_sample={samples}"]
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        listing, joined = Path(scratch) / "concat.txt", Path(scratch) / "joined.mov"
        listing.write_text("".join(_concat_entry(clips[shot.index]) for shot in shots), encoding="utf-8")
        run(["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v:0", "-c", "copy", "-an", str(joined)])
        got = probe(joined).frames
        if got != info.frames:
            raise CineRecastError(f"assembled {'preview' if preview else 'master'} has {got} frames, "
                                  f"the reference has {info.frames}")
        if preview:
            out = pp.out_dir / "preview.mp4"
            args = ["-i", str(joined)] + (["-i", str(sound_file)] if sound_file else []) + ["-map", "0:v:0"]
            args += ["-map", "1:a:0", *fit, "-c:a", "aac", "-b:a", "256k"] if sound_file else ["-an"]
            run([*args, *_H264, "-movflags", "+faststart", str(out)])
            return Assembled(out, described)
        master, final = pp.out_dir / "master.mov", pp.out_dir / "final.mp4"
        args = ["-i", str(joined)] + (["-i", str(sound_file)] if sound_file else []) + ["-map", "0:v:0"]
        args += ["-map", "1:a:0", *fit, "-c:a", "pcm_s24le"] if sound_file else ["-an"]
        run([*args, "-c:v", "copy", str(master)])
    got = probe(master).frames
    if got != info.frames:
        raise CineRecastError(f"assembled master has {got} frames, the reference has {info.frames}")
    args = ["-i", str(master), "-map", "0:v:0"]
    args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "256k"] if sound_file else ["-an"]
    run([*args, *_H264, "-movflags", "+faststart", str(final)])
    return Assembled(final, described)


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
