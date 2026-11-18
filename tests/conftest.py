"""Synthetic clip helpers shared by every test. Clips are generated with ffmpeg lavfi sources."""

import subprocess
from pathlib import Path

import pytest


def _encode_args(path: Path) -> list[str]:
    return ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-fps_mode", "passthrough", str(path)]


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-400:]}")


def make_clip(path: Path, segments: list[tuple[str, int]], *, size: tuple[int, int] = (384, 216),
              fps: str = "24", bars: int = 0, side_bars: int = 0, logo: bool = False,
              dark: bool = False, audio: bool = False) -> Path:
    """Build a clip from lavfi sources. segments: (source name, frame count) pairs.

    A source may carry its own options, such as "color=c=white"; size and rate are appended.

    bars is the pixel height of black bands at the top and bottom (total size stays `size`).
    side_bars is the pixel width of black bands at the left and right.
    logo draws a 24x12 white box at the left of the top band.
    dark applies a low-contrast lifted-black grade.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    args: list[str] = []
    chains: list[str] = []
    for i, (source, frames) in enumerate(segments):
        sep = ":" if "=" in source else "="
        args += ["-f", "lavfi", "-i", f"{source}{sep}size={w}x{h}:rate={fps}"]
        chains.append(f"[{i}:v]trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{i}]")
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    joined = "".join(f"[v{i}]" for i in range(len(segments)))
    post: list[str] = []
    if dark:
        post.append("lutyuv=y=val*0.25+18")
    if bars > 0:
        post.append(f"drawbox=x=0:y=0:w=iw:h={bars}:color=black:t=fill")
        post.append(f"drawbox=x=0:y=ih-{bars}:w=iw:h={bars}:color=black:t=fill")
    if side_bars > 0:
        post.append(f"drawbox=x=0:y=0:w={side_bars}:h=ih:color=black:t=fill")
        post.append(f"drawbox=x=iw-{side_bars}:y=0:w={side_bars}:h=ih:color=black:t=fill")
    if logo:
        post.append(f"drawbox=x={side_bars + 8}:y={max(0, (bars - 12) // 2)}:w=24:h=12:color=white:t=fill")
    post.append("format=yuv420p")
    chains.append(f"{joined}concat=n={len(segments)}:v=1:a=0[c]")
    chains.append("[c]" + ",".join(post) + "[out]")
    args += ["-filter_complex", ";".join(chains), "-map", "[out]"]
    if audio:
        args += ["-map", f"{len(segments)}:a", "-c:a", "aac", "-shortest"]
    args += _encode_args(path)
    _run(args)
    return path


def make_motion_clip(path: Path, kind: str, *, frames: int = 24, rate: float = 4.0,
                     size: tuple[int, int] = (384, 216)) -> Path:
    """A clip whose content is one fixed textured image, moved per frame.

    kind: "static", "pan" (content moves right by `rate` pixels per frame, left when `rate` is
    negative) or "zoom" (content magnified by `rate` times per frame).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    if kind == "static":
        graph = (f"testsrc2=size={w}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
                 "setpts=N/(24*TB),format=yuv420p")
    elif kind == "pan":
        step = abs(rate)
        pad = int(step * frames) + 2
        x = f"{pad - 2}-n*{step}" if rate >= 0 else f"2+n*{step}"   # the window moves against the content
        graph = (f"testsrc2=size={w + pad}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
                 f"setpts=N/(24*TB),crop=w={w}:h={h}:x='{x}':y=0,format=yuv420p")
    elif kind == "zoom":
        graph = (f"testsrc2=size={w}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
                 f"setpts=N/(24*TB),scale=w='trunc(iw*pow({rate},n)/2)*2':h='trunc(ih*pow({rate},n)/2)*2':eval=frame,"
                 f"crop=w={w}:h={h}:x='(trunc({w}*pow({rate},n)/2)*2-{w})/2':"
                 f"y='(trunc({h}*pow({rate},n)/2)*2-{h})/2',format=yuv420p")
    else:
        raise ValueError(f"unknown motion kind: {kind}")
    _run(["-f", "lavfi", "-i", graph, "-frames:v", str(frames), *_encode_args(path)])
    return path


def make_sweep_clip(path: Path, *, frames: int = 40, sweep_start: int = 18,
                    size: tuple[int, int] = (384, 216)) -> Path:
    """One static smptebars shot, no cut, crossed by a fast occluder.

    A full-height black box 5/12 of the frame wide is just off the left edge at frame
    sweep_start and just off the right edge at frame sweep_start + 5, eased so it moves fastest
    in the middle. The frame differences form a broad peak instead of one isolated spike.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    box_w = w * 5 // 12
    p = f"clip((t*24-{sweep_start})/5,0,1)"
    x = f"{w + box_w}*{p}*{p}*(3-2*{p})-{box_w}"
    graph = (f"[0:v]trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[bg];"
             f"[1:v]setsar=1,format=yuv420p[box];"
             f"[bg][box]overlay=x='{x}':y=0:eval=frame:shortest=1,format=yuv420p[out]")
    _run(["-f", "lavfi", "-i", f"smptebars=size={w}x{h}:rate=24",
          "-f", "lavfi", "-i", f"color=c=black:size={box_w}x{h}:rate=24",
          "-filter_complex", graph, "-map", "[out]", "-frames:v", str(frames), *_encode_args(path)])
    return path


@pytest.fixture(scope="session")
def clip3(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "clip3.mp4"
    return make_clip(path, [("testsrc2", 30), ("smptebars", 24), ("mandelbrot", 36)])


@pytest.fixture
def project3(tmp_path, clip3):
    from cine_recast.project import init_project
    return init_project(tmp_path / "p", clip3, "t")


@pytest.fixture(scope="session")
def _analyzed3_template(tmp_path_factory, clip3) -> Path:
    """Analyse clip3 once per session; tests get copies of this folder."""
    from cine_recast.analyze import analyze
    from cine_recast.project import init_project

    root = tmp_path_factory.mktemp("analyzed3") / "p"
    init_project(root, clip3, "t")
    analyze(root)
    return root


@pytest.fixture
def analyzed3(_analyzed3_template, tmp_path):
    """An isolated copy of the analysed three-shot project."""
    import shutil

    from cine_recast.paths import ProjectPaths

    root = tmp_path / "p"
    shutil.copytree(_analyzed3_template, root)
    return ProjectPaths(root)


CAST_ELEMENTS = [
    {"id": "hero", "kind": "person", "name": "Rider", "shots": [1, 3],
     "look": "teenage boy, tousled brown hair, green wool tunic, dark leather vest"},
    {"id": "sky_battle", "kind": "background", "name": "Sky around the giant", "shots": [1, 3],
     "look": "grey-green smoke, falling rock debris, overcast light"},
]


def png(path: Path) -> Path:
    """Write a 1x1 PNG at path (making its folder) and return the path."""
    from PIL import Image

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1, 1), (128, 128, 128)).save(path)
    return path


def run_cli(commands: list[list]) -> None:
    """Run each command through the CLI in order (arguments may be paths); each must exit with 0."""
    from cine_recast.cli import main

    for command in commands:
        args = [str(arg) for arg in command]
        assert main(args) == 0, f"cine-recast {' '.join(args)} did not exit with 0"


def good_casting(root: Path, tmp_path: Path) -> None:
    """Write a valid casting.json with generated 1x1 PNG images and answer all three rights items."""
    from cine_recast.jsonio import write_json
    from cine_recast.paths import ProjectPaths
    from cine_recast.project import RIGHTS_ITEMS, set_right

    refs = Path(tmp_path) / "refs"
    person, background = png(refs / "person_front.png"), png(refs / "background.png")
    write_json(ProjectPaths(Path(root)).casting_file, {
        "elements": CAST_ELEMENTS,
        "swaps": [
            {"element": "hero", "images": [person.as_posix()], "note": ""},
            {"element": "sky_battle", "images": [background.as_posix()], "note": ""},
        ],
    })
    for item in RIGHTS_ITEMS:
        set_right(root, item, "yes")


def _copy_project(template: Path, tmp_path: Path):
    """An isolated copy of a session-built project folder."""
    import shutil

    from cine_recast.paths import ProjectPaths

    root = Path(tmp_path) / "p"
    shutil.copytree(template, root)
    return ProjectPaths(root)


EXAMPLE_MODEL = {
    "id": "seedance_2_5", "kind": "video", "min_seconds": 4, "max_seconds": 15,
    "aspect_ratios": ["21:9", "16:9"], "resolutions": ["480p", "720p", "1080p", "4k"],
    "modes": ["video_edit", "omni_reference"], "video_ref": True, "image_refs": 4,
    "read_at": "2026-11-18",
}


def write_models(root: Path, **overrides) -> None:
    """Write models.json holding the example model, with `overrides` replacing its fields."""
    from cine_recast.jsonio import write_json
    from cine_recast.paths import ProjectPaths

    write_json(ProjectPaths(Path(root)).models_file, {"models": [{**EXAMPLE_MODEL, **overrides}]})


IMAGE_MODEL = {
    "id": "image_model_x", "kind": "image", "aspect_ratios": ["1:1", "3:4", "16:9", "21:9"],
    "resolutions": ["1k", "2k"], "image_refs": 4, "read_at": "2026-11-18",
}


@pytest.fixture(scope="session")
def _assets3_template(tmp_path_factory, _analyzed3_template) -> Path:
    """analyzed3 + the elements without swaps + a video and an image model + rights for generated
    swaps (reference and background "yes", likeness "not_applicable"), built once per session."""
    import shutil

    from cine_recast.jsonio import write_json
    from cine_recast.paths import ProjectPaths
    from cine_recast.project import set_right

    root = tmp_path_factory.mktemp("assets3") / "p"
    shutil.copytree(_analyzed3_template, root)
    pp = ProjectPaths(root)
    write_json(pp.casting_file, {"elements": CAST_ELEMENTS, "swaps": []})
    write_json(pp.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL]})
    set_right(root, "reference_rights", "yes")
    set_right(root, "background_rights", "yes")
    set_right(root, "likeness_consent", "not_applicable")
    return root


@pytest.fixture
def assets3(_assets3_template, tmp_path):
    """An isolated copy of the asset project: elements hero (person) and sky_battle (background), no swaps."""
    return _copy_project(_assets3_template, tmp_path)


def generate_and_use(root: Path, element: str, tmp_path: Path, refs: list[Path] = ()) -> str:
    """Run one asset of `element` (with reference images `refs`) through add, set-cost, approve,
    issue and done, put its image in the swap table with assets.use, and return that image's
    project-relative path."""
    from cine_recast import assets, ledger

    batch = f"gen-{element}"
    asset = assets.add(root, element=element, prompt="A generated stand-in in neutral studio light.",
                       model="image_model_x", batch=batch, refs=list(refs))
    assets.set_cost(root, asset["id"], 1.0)
    ledger.approve(root, batch, 1.0, "owner")
    assets.issue_next(root, batch)
    image = png(Path(tmp_path) / "generated" / f"{asset['id']}.png")
    stored = assets.record_done(root, asset["id"], "prov-gen", 1.0, [image])["files"][0]
    assets.use(root, element, stored)
    return stored


def set_prompt(root: Path, unit_id: str, text: str) -> None:
    """Set the prompt of one unit in plan.json, as an agent editing the file would."""
    from cine_recast.jsonio import read_json, write_json
    from cine_recast.paths import ProjectPaths

    path = ProjectPaths(Path(root)).plan_file
    data = read_json(path)
    matches = [u for u in data["units"] if u["id"] == unit_id]
    if not matches:
        raise KeyError(unit_id)
    matches[0]["prompt"] = text
    write_json(path, data)


GOOD_PROMPT = "Replace the rider with the person in image 1 and the sky with image 2."


@pytest.fixture(scope="session")
def _cast3_template(tmp_path_factory, _analyzed3_template) -> Path:
    """The analysed three-shot project with a valid casting.json, built once per session."""
    import shutil

    base = tmp_path_factory.mktemp("cast3")
    root = base / "p"
    shutil.copytree(_analyzed3_template, root)
    good_casting(root, base)  # the swap images live in base/refs, outside the project
    return root


@pytest.fixture
def cast3(_cast3_template, tmp_path):
    """An isolated copy of the cast three-shot project (analyzed3 + good_casting)."""
    return _copy_project(_cast3_template, tmp_path)


@pytest.fixture(scope="session")
def _planned3_template(tmp_path_factory, _cast3_template) -> Path:
    """cast3 + the example model + an edit plan for batch "b" with English prompts, built once."""
    import shutil

    from cine_recast.plan import build

    root = tmp_path_factory.mktemp("planned3") / "p"
    shutil.copytree(_cast3_template, root)
    write_models(root)
    plan = build(root, model="seedance_2_5", method="edit", batch="b")
    for unit in plan["units"]:
        set_prompt(root, unit["id"], GOOD_PROMPT)
    return root


@pytest.fixture
def planned3(_planned3_template, tmp_path):
    """An isolated copy of the planned project: one unit u01 (shots 1-3), one job j0001."""
    return _copy_project(_planned3_template, tmp_path)


@pytest.fixture(scope="session")
def _conformed3_template(tmp_path_factory, _planned3_template) -> Path:
    """planned3 with one fake take of u01 (fake_take defaults), conformed, built once per session."""
    import shutil

    from cine_recast.conform import conform

    base = tmp_path_factory.mktemp("conformed3")
    root = base / "p"
    shutil.copytree(_planned3_template, root)
    take_id = fake_take(root, "u01", base)  # the source clip lives in base/fake_takes, outside the project
    conform(root, "u01", take_id)
    return root


@pytest.fixture
def conformed3(_conformed3_template, tmp_path):
    """An isolated copy of the conformed project: unit u01 with one conformed take, its id on `take_id`."""
    pp = _copy_project(_conformed3_template, tmp_path)
    pp.take_id = "t001"
    return pp


def planned_project(root: Path, clip: Path):
    """Build a project from `clip` up to a plan that must hold exactly one unit.

    init, analyze, an empty casting with every rights item answered, the example model, then
    plan build. Returns the ProjectPaths; the plan's unit id is on it as `unit_id`.
    """
    from cine_recast.analyze import analyze
    from cine_recast.jsonio import write_json
    from cine_recast.plan import build
    from cine_recast.project import RIGHTS_ITEMS, init_project, set_right

    root = Path(root)
    pp = init_project(root, clip, "t")
    analyze(root)
    write_json(pp.casting_file, {"elements": [], "swaps": []})
    for item in RIGHTS_ITEMS:
        set_right(root, item, "yes")
    write_models(root)
    units = build(root, model="seedance_2_5", method="edit", batch="b")["units"]
    if len(units) != 1:
        raise RuntimeError(f"planned_project expects one unit, the plan has {[u['id'] for u in units]}")
    pp.unit_id = units[0]["id"]
    return pp


def fake_raw(root: Path, unit_id: str, tmp_path: Path, *, size: tuple[int, int] = (640, 360),
             fps: str | None = None, extra_frames: int = 12, filters: str = "") -> Path:
    """Write a stand-in for the clip a generation job returns for a unit, made from the unit's
    reference clip, and return its path (under tmp_path, outside the project).

    The clip is scaled to `size`, converted to frame rate `fps` when given (None keeps the
    reference rate), passed through `filters` (an ffmpeg filter chain) when given, and then gets
    its last frame repeated `extra_frames` times, or loses that many frames from its end when
    extra_frames is negative. extra_frames counts in the clip's own frame rate.
    """
    from cine_recast.ffmpeg import probe
    from cine_recast.paths import ProjectPaths

    source = ProjectPaths(Path(root)).unit_dir(unit_id) / "ref.mp4"
    folder = Path(tmp_path) / "fake_takes"
    folder.mkdir(parents=True, exist_ok=True)
    number = 1
    while (folder / f"take{number:03d}.mp4").exists():
        number += 1
    out = folder / f"take{number:03d}.mp4"
    chain = [f"scale={size[0]}:{size[1]}", "setsar=1"]
    if fps is not None:
        chain.append(f"fps={fps}")
    if filters:
        chain.append(filters)
    if extra_frames > 0:
        chain.append(f"tpad=stop_mode=clone:stop={extra_frames}")
    _run(["-i", str(source), "-vf", ",".join(chain), "-an", *_encode_args(out)])
    if extra_frames < 0:
        whole = out.with_name(f"{out.stem}_whole.mp4")
        out.replace(whole)
        keep = probe(whole).frames + extra_frames
        if keep <= 0:
            raise ValueError(f"extra_frames={extra_frames} leaves no frames")
        _run(["-i", str(whole), "-vf", f"trim=end_frame={keep},setpts=PTS-STARTPTS", "-an", *_encode_args(out)])
    return out


def fake_take(root: Path, unit_id: str, tmp_path: Path, **options) -> str:
    """Register a stand-in take for a unit (the clip of fake_raw, same options) and return its id."""
    from cine_recast.takes import new_take

    return new_take(root, unit_id, fake_raw(root, unit_id, tmp_path, **options), {"job": "fake"})


@pytest.fixture(scope="session")
def _finished3_template(tmp_path_factory, _conformed3_template) -> Path:
    """conformed3 + an approved, issued and finished job, a scored take, and that take selected."""
    import shutil

    from cine_recast import ledger
    from cine_recast.plan import issue_next, set_cost
    from cine_recast.qc import score_take
    from cine_recast.takes import select

    root = tmp_path_factory.mktemp("finished3") / "p"
    shutil.copytree(_conformed3_template, root)
    set_cost(root, "j0001", 40.0)
    ledger.approve(root, "b", 50.0, "owner")
    issue_next(root, "b")
    ledger.done(root, "j0001", "prov-1", 38.0)
    score_take(root, "u01", "t001")
    select(root, "u01", "t001")
    return root


@pytest.fixture
def finished3(_finished3_template, tmp_path):
    """An isolated copy of the finished project: unit u01, take t001 conformed, scored and selected."""
    return _copy_project(_finished3_template, tmp_path)
