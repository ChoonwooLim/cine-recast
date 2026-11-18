import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import __version__, assemble, assets, conform, extras, ledger, plan, qc, report, review, sound, soundtrack, takes
from .analyze import analyze, review_strips
from .casting import check as check_casting
from .casting import write_template
from .errors import CineRecastError
from .project import RIGHT_ANSWERS, RIGHTS_ITEMS, init_project, load_project, set_right


def _parse_cuts(text: str) -> list[int]:
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise CineRecastError(f"--cuts must be comma-separated frame numbers: {text}") from exc


def _cmd_init(args: argparse.Namespace) -> int:
    pp = init_project(Path(args.project), Path(args.reference), args.name)
    print(f"project created: {pp.root}")
    return 0


def _cmd_rights_set(args: argparse.Namespace) -> int:
    set_right(Path(args.project), args.item, args.answer, args.note)
    print(f"{args.item}: {args.answer}")
    return 0


def _parse_picture(text: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in text.split(",")]
    try:
        values = tuple(int(part) for part in parts)
    except ValueError:
        values = ()
    if len(values) != 4:
        raise CineRecastError(f"--picture must be four whole numbers x,y,w,h separated by commas: {text}")
    return values


def _cmd_analyze(args: argparse.Namespace) -> int:
    cuts = _parse_cuts(args.cuts) if args.cuts is not None else None
    picture = _parse_picture(args.picture) if args.picture is not None else None
    result = analyze(Path(args.project), cuts=cuts, picture=picture)
    print(f"shots: {result['shots']}  cuts: {result['cuts']}  picture: {result['picture_box']}")
    return 0


def _cmd_cuts_review(args: argparse.Namespace) -> int:
    for path in review_strips(Path(args.project)):
        print(path)
    return 0


def _cmd_cast_template(args: argparse.Namespace) -> int:
    print(write_template(Path(args.project)))
    return 0


def _cmd_cast_check(args: argparse.Namespace) -> int:
    problems = check_casting(Path(args.project))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 2
    print("casting ok")
    return 0


def _print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _report_problems(problems: list[str], ok_text: str) -> int:
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 2
    print(ok_text)
    return 0


def _cmd_models_check(args: argparse.Namespace) -> int:
    return _report_problems(plan.check_models(Path(args.project)), "models ok")


def _cmd_plan_build(args: argparse.Namespace) -> int:
    data = plan.build(Path(args.project), model=args.model, method=args.method, batch=args.batch,
                      quality=args.quality, max_unit_seconds=args.max_unit_seconds, replace=args.replace,
                      per_shot=args.per_shot)
    for unit in data["units"]:
        padded = f"  padded +{unit['pad_frames']} frames" if unit.get("pad_frames") else ""
        print(f"{unit['id']}: shots {unit['shots']}  frames {unit['start']}-{unit['end']}  "
              f"duration {unit['params']['duration']} s{padded}")
    print(f"planned jobs: {', '.join(job['id'] for job in data['jobs'])} (batch {args.batch})")
    return 0


def _cmd_plan_unit(args: argparse.Namespace) -> int:
    unit = plan.refresh_unit(Path(args.project), args.unit, model=args.model, method=args.method)
    padded = f"  padded +{unit['pad_frames']} frames" if unit.get("pad_frames") else ""
    print(f"{unit['id']}: model {unit['model']}, method {unit['method']}, duration {unit['params']['duration']} s, "
          f"inputs: {', '.join(entry['path'] for entry in unit['inputs'])}{padded}")
    return 0


def _cmd_plan_check(args: argparse.Namespace) -> int:
    return _report_problems(plan.check(Path(args.project)), "plan ok")


def _cmd_plan_show(args: argparse.Namespace) -> int:
    _print_json(plan.load(Path(args.project)))
    return 0


def _cmd_jobs_list(args: argparse.Namespace) -> int:
    _print_json(plan.list_jobs(Path(args.project)))
    return 0


def _cmd_jobs_add(args: argparse.Namespace) -> int:
    print(plan.add_job(Path(args.project), args.unit, args.batch, args.quality)["id"])
    return 0


def _cmd_jobs_set_cost(args: argparse.Namespace) -> int:
    job = plan.set_cost(Path(args.project), args.job, args.credits)
    print(f"{job['id']}: quoted {job['est_credits']} credits")
    return 0


def _cmd_jobs_next(args: argparse.Namespace) -> int:
    try:
        issued = plan.issue_next(Path(args.project), args.batch)
    except plan.IssueRefused as exc:
        _print_json(exc.issued)  # jobs issued before the refusal must reach the agent
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    _print_json(issued)
    return 0


def _cmd_jobs_done(args: argparse.Namespace) -> int:
    result = plan.record_done(Path(args.project), args.job, args.provider_id, args.credits, Path(args.file))
    print(f"{args.job}: done, unit {result['unit']} take {result['take']}")
    return 0


def _cmd_jobs_fail(args: argparse.Namespace) -> int:
    plan.record_fail(Path(args.project), args.job, args.reason, args.credits)
    print(f"{args.job}: failed")
    return 0


def _cmd_assets_add(args: argparse.Namespace) -> int:
    refs = [Path(ref) for ref in args.ref or []]
    asset = assets.add(Path(args.project), element=args.element, prompt=args.prompt, model=args.model,
                       batch=args.batch, aspect_ratio=args.aspect_ratio, count=args.count, refs=refs)
    print(asset["id"])
    return 0


def _cmd_assets_list(args: argparse.Namespace) -> int:
    _print_json(assets.list_assets(Path(args.project)))
    return 0


def _cmd_assets_set_cost(args: argparse.Namespace) -> int:
    asset = assets.set_cost(Path(args.project), args.asset, args.credits)
    print(f"{asset['id']}: quoted {asset['est_credits']} credits")
    return 0


def _cmd_assets_next(args: argparse.Namespace) -> int:
    try:
        issued = assets.issue_next(Path(args.project), args.batch)
    except plan.IssueRefused as exc:
        _print_json(exc.issued)  # assets issued before the refusal must reach the agent
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    _print_json(issued)
    return 0


def _cmd_assets_done(args: argparse.Namespace) -> int:
    result = assets.record_done(Path(args.project), args.asset, args.provider_id, args.credits,
                                [Path(file) for file in args.file])
    print(f"{args.asset}: done, files {', '.join(result['files'])}")
    return 0


def _cmd_assets_fail(args: argparse.Namespace) -> int:
    assets.record_fail(Path(args.project), args.asset, args.reason, args.credits)
    print(f"{args.asset}: failed")
    return 0


def _cmd_assets_use(args: argparse.Namespace) -> int:
    assets.use(Path(args.project), args.element, args.image)
    print(f"{args.element}: swap image {args.image}")
    return 0


def _cmd_extra_add(args: argparse.Namespace) -> int:
    item = extras.add(Path(args.project), label=args.label, prompt=args.prompt, model=args.model,
                      seconds=args.seconds, batch=args.batch, extend=args.extend, direction=args.direction,
                      start=args.start, end=args.end, refs=args.ref or [], quality=args.quality,
                      aspect_ratio=args.aspect_ratio)
    print(item["id"])
    return 0


def _cmd_extra_list(args: argparse.Namespace) -> int:
    _print_json(extras.list_extras(Path(args.project)))
    return 0


def _cmd_extra_set_cost(args: argparse.Namespace) -> int:
    item = extras.set_cost(Path(args.project), args.extra, args.credits)
    print(f"{item['id']}: quoted {item['est_credits']} credits")
    return 0


def _cmd_extra_next(args: argparse.Namespace) -> int:
    try:
        issued = extras.issue_next(Path(args.project), args.batch)
    except extras.IssueRefused as exc:
        _print_json(exc.issued)  # extras issued before the refusal must reach the agent
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    _print_json(issued)
    return 0


def _cmd_extra_done(args: argparse.Namespace) -> int:
    result = extras.record_done(Path(args.project), args.extra, args.provider_id, args.credits, Path(args.file))
    print(f"{args.extra}: done, result {result['result']}")
    return 0


def _cmd_extra_fail(args: argparse.Namespace) -> int:
    extras.record_fail(Path(args.project), args.extra, args.reason, args.credits)
    print(f"{args.extra}: failed")
    return 0


def _budget_line(budget: dict) -> str:
    return (f"budget: limit {budget['limit']}, approved {budget['approved']}, remaining {budget['remaining']} "
            f"(by {budget['by']})")


def _cmd_sound_add(args: argparse.Namespace) -> int:
    item = sound.add(Path(args.project), kind=args.kind, label=args.label, prompt=args.prompt, model=args.model,
                     seconds=args.seconds, batch=args.batch, at_frame=args.at_frame, shot=args.shot,
                     offset_frames=args.offset_frames, gain_db=args.gain_db, fade_in=args.fade_in,
                     fade_out=args.fade_out)
    print(item["id"])
    return 0


def _cmd_sound_list(args: argparse.Namespace) -> int:
    _print_json(sound.list_sounds(Path(args.project)))
    return 0


def _cmd_sound_set_cost(args: argparse.Namespace) -> int:
    item = sound.set_cost(Path(args.project), args.sound, args.credits)
    print(f"{item['id']}: quoted {item['est_credits']} credits")
    return 0


def _cmd_sound_next(args: argparse.Namespace) -> int:
    try:
        issued = sound.issue_next(Path(args.project), args.batch)
    except sound.IssueRefused as exc:
        _print_json(exc.issued)  # sounds issued before the refusal must reach the agent
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    _print_json(issued)
    return 0


def _cmd_sound_done(args: argparse.Namespace) -> int:
    result = sound.record_done(Path(args.project), args.sound, args.provider_id, args.credits, Path(args.file))
    print(f"{args.sound}: done, file {result['file']}")
    return 0


def _cmd_sound_fail(args: argparse.Namespace) -> int:
    sound.record_fail(Path(args.project), args.sound, args.reason, args.credits)
    print(f"{args.sound}: failed")
    return 0


def _cmd_sound_set(args: argparse.Namespace) -> int:
    root = Path(args.project)
    item = sound.set_placement(root, args.sound, at_frame=args.at_frame, shot=args.shot,
                               offset_frames=args.offset_frames, gain_db=args.gain_db, fade_in=args.fade_in,
                               fade_out=args.fade_out, mute=args.mute, unmute=args.unmute)
    print(sound.placement_line(root, item))
    return 0


def _cmd_sound_mix(args: argparse.Namespace) -> int:
    result = soundtrack.mix(Path(args.project))
    correlation = result["reference_correlation"]
    print(result["path"])
    print(f"loudness {result['loudness_after']} LUFS  peak {result['peak_dbfs']} dBFS  reference correlation "
          f"{'none (the reference has no sound)' if correlation is None else correlation}")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    root = Path(args.project)
    load_project(root)  # never start a ledger outside a project folder
    record = ledger.approve(root, args.batch, args.limit, args.by, args.note)
    status = ledger.batch_status(root, record["batch"])
    print(f"approved batch {record['batch']}: +{record['limit_credits']} credits, limit now {status['limit']}, "
          f"committed {status['committed']}, remaining {status['remaining']} (by {record['by']})")
    budget = ledger.budget_status(root)
    if budget is not None:
        print(_budget_line(budget))
    return 0


def _cmd_budget_set(args: argparse.Namespace) -> int:
    root = Path(args.project)
    load_project(root)  # never start a ledger outside a project folder
    ledger.set_budget(root, args.limit, args.by, args.note)
    print(_budget_line(ledger.budget_status(root)))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    root = Path(args.project)
    load_project(root)  # a mistyped folder must not read as a clean audit
    report = ledger.audit(root)
    _print_json(report)
    return 0 if report["ok"] else 2


def _cmd_takes_select(args: argparse.Namespace) -> int:
    takes.select(Path(args.project), args.unit, args.take)
    print(f"{args.unit}: selected take {args.take}")
    return 0


def _cmd_takes_review(args: argparse.Namespace) -> int:
    for path in review.review_take(Path(args.project), args.unit, args.take):
        print(path)
    return 0


def _cmd_takes_confirm(args: argparse.Namespace) -> int:
    confirmation = review.confirm(Path(args.project), args.unit, args.take, args.by, args.note)
    print(f"{args.unit}: take {confirmation['take']} confirmed by {confirmation['by']}")
    return 0


def _cmd_takes_cuts(args: argparse.Namespace) -> int:
    _print_json(conform.scan_take(Path(args.project), args.unit, args.take))
    return 0


def _parse_take_cuts(text: str) -> list[int]:
    try:
        return [int(part) for part in text.split(",")]
    except ValueError as exc:
        raise CineRecastError(f"--take-cuts must be whole take frame numbers separated by commas, e.g. "
                              f"\"0,53,105\": {text}") from exc


def _cmd_conform(args: argparse.Namespace) -> int:
    options = {"start_frame": args.start_frame, "allow_retime": args.allow_retime}
    if args.take_cuts is not None:
        options["take_cuts"] = _parse_take_cuts(args.take_cuts)
    paths = conform.conform(Path(args.project), args.unit, args.take, **options)
    for path in paths:
        print(path)
    return 0


def _cmd_qc(args: argparse.Namespace) -> int:
    _print_json(qc.score_take(Path(args.project), args.unit, args.take))
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    audio = Path(args.audio) if args.audio else None
    result = assemble.assemble(Path(args.project), audio=audio, silent=args.silent, preview=args.preview)
    print(result.path)
    print(f"sound: {result.sound}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    print(assemble.compare(Path(args.project)))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    print(report.write_report(Path(args.project)))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cine-recast")
    parser.add_argument("--version", action="version", version=f"cine-recast {__version__}")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="create a project from a reference video")
    init.add_argument("project", help="project folder (must not exist or be empty)")
    init.add_argument("--reference", required=True, help="reference video file")
    init.add_argument("--name", required=True, help="project name")
    init.set_defaults(func=_cmd_init)

    rights = sub.add_parser("rights", help="record rights answers")
    rights_sub = rights.add_subparsers(dest="rights_command")
    rights_set = rights_sub.add_parser("set", help="record one rights answer")
    rights_set.add_argument("project", help="project folder")
    rights_set.add_argument("--item", required=True, choices=RIGHTS_ITEMS)
    rights_set.add_argument("--answer", required=True, choices=RIGHT_ANSWERS)
    rights_set.add_argument("--note", default="")
    rights_set.set_defaults(func=_cmd_rights_set)

    analyze_p = sub.add_parser("analyze", help="analyze the reference video of a project")
    analyze_p.add_argument("project", help="project folder")
    analyze_p.add_argument("--cuts", help="comma-separated cut frames that replace detection, e.g. 88,130")
    analyze_p.add_argument("--picture", help="picture area x,y,w,h (even numbers, inside the frame) that replaces "
                                             "detection, e.g. 0,46,640,268")
    analyze_p.set_defaults(func=_cmd_analyze)

    cuts = sub.add_parser("cuts", help="cut tools")
    cuts_sub = cuts.add_subparsers(dest="cuts_command")
    review = cuts_sub.add_parser("review", help="write review images for the cuts and shots")
    review.add_argument("project", help="project folder")
    review.set_defaults(func=_cmd_cuts_review)

    cast = sub.add_parser("cast", help="elements and swap table")
    cast_sub = cast.add_subparsers(dest="cast_command")
    cast_template = cast_sub.add_parser("template", help="write casting.template.json")
    cast_template.add_argument("project", help="project folder")
    cast_template.set_defaults(func=_cmd_cast_template)
    cast_check = cast_sub.add_parser("check", help="validate casting.json")
    cast_check.add_argument("project", help="project folder")
    cast_check.set_defaults(func=_cmd_cast_check)

    models = sub.add_parser("models", help="model list (models.json)")
    models_sub = models.add_subparsers(dest="models_command")
    models_check = models_sub.add_parser("check", help="validate models.json")
    models_check.add_argument("project", help="project folder")
    models_check.set_defaults(func=_cmd_models_check)

    plan_p = sub.add_parser("plan", help="generation units")
    plan_sub = plan_p.add_subparsers(dest="plan_command")
    plan_build = plan_sub.add_parser("build", help="pack shots into units and plan one job per unit")
    plan_build.add_argument("project", help="project folder")
    plan_build.add_argument("--model", required=True, help="model id from models.json")
    plan_build.add_argument("--method", required=True, choices=plan.METHODS)
    plan_build.add_argument("--batch", required=True, help="approval batch of the planned jobs")
    plan_build.add_argument("--quality", choices=plan.QUALITIES, default="draft")
    plan_build.add_argument("--max-unit-seconds", type=float, default=None,
                            help="unit length cap below the model's max_seconds")
    plan_build.add_argument("--per-shot", action="store_true",
                            help="make every shot a unit of its own (not with --max-unit-seconds)")
    plan_build.add_argument("--replace", action="store_true",
                            help="rebuild an existing plan: archive it under plans/ and give the new units and "
                                 "jobs ids never used before (refused while a job is issued and not finished)")
    plan_build.set_defaults(func=_cmd_plan_build)
    plan_unit = plan_sub.add_parser("unit", help="bring one unit up to date: its inputs from the swap table as it is "
                                                 "now, and optionally another model or method (refused while one of "
                                                 "its jobs is issued and not finished)")
    plan_unit.add_argument("project", help="project folder")
    plan_unit.add_argument("--unit", required=True)
    plan_unit.add_argument("--model", default=None, help="video model id from models.json (default: the unit's)")
    plan_unit.add_argument("--method", choices=plan.METHODS, default=None, help="default: the unit's")
    plan_unit.set_defaults(func=_cmd_plan_unit)
    plan_check = plan_sub.add_parser("check", help="check prompts, input files and model limits")
    plan_check.add_argument("project", help="project folder")
    plan_check.set_defaults(func=_cmd_plan_check)
    plan_show = plan_sub.add_parser("show", help="print plan.json")
    plan_show.add_argument("project", help="project folder")
    plan_show.set_defaults(func=_cmd_plan_show)

    jobs = sub.add_parser("jobs", help="generation jobs")
    jobs_sub = jobs.add_subparsers(dest="jobs_command")
    jobs_list = jobs_sub.add_parser("list", help="print the jobs and the params each is sent with, as JSON")
    jobs_list.add_argument("project", help="project folder")
    jobs_list.set_defaults(func=_cmd_jobs_list)
    jobs_add = jobs_sub.add_parser("add", help="add a planned job for a unit")
    jobs_add.add_argument("project", help="project folder")
    jobs_add.add_argument("--unit", required=True)
    jobs_add.add_argument("--batch", required=True)
    jobs_add.add_argument("--quality", required=True, choices=plan.QUALITIES)
    jobs_add.set_defaults(func=_cmd_jobs_add)
    jobs_cost = jobs_sub.add_parser("set-cost", help="record a job's quoted cost")
    jobs_cost.add_argument("project", help="project folder")
    jobs_cost.add_argument("--job", required=True)
    jobs_cost.add_argument("--credits", required=True, type=float)
    jobs_cost.set_defaults(func=_cmd_jobs_set_cost)
    jobs_next = jobs_sub.add_parser("next", help="issue the batch's quoted jobs through the ledger")
    jobs_next.add_argument("project", help="project folder")
    jobs_next.add_argument("--batch", required=True)
    jobs_next.set_defaults(func=_cmd_jobs_next)
    jobs_done = jobs_sub.add_parser("done", help="record a finished job and store its file as a take")
    jobs_done.add_argument("project", help="project folder")
    jobs_done.add_argument("--job", required=True)
    jobs_done.add_argument("--provider-id", required=True)
    jobs_done.add_argument("--credits", required=True, type=float)
    jobs_done.add_argument("--file", required=True)
    jobs_done.set_defaults(func=_cmd_jobs_done)
    jobs_fail = jobs_sub.add_parser("fail", help="record a failed job")
    jobs_fail.add_argument("project", help="project folder")
    jobs_fail.add_argument("--job", required=True)
    jobs_fail.add_argument("--reason", required=True)
    jobs_fail.add_argument("--credits", type=float, default=0.0)
    jobs_fail.set_defaults(func=_cmd_jobs_fail)

    assets_p = sub.add_parser("assets", help="generated swap images (assets.json)")
    assets_sub = assets_p.add_subparsers(dest="assets_command")
    assets_add = assets_sub.add_parser("add", help="plan an image-generation job for an element")
    assets_add.add_argument("project", help="project folder")
    assets_add.add_argument("--element", required=True, help="element id from casting.json")
    assets_add.add_argument("--prompt", required=True, help="English description of the image")
    assets_add.add_argument("--model", required=True, help="image model id from models.json")
    assets_add.add_argument("--batch", required=True, help="approval batch of the asset")
    assets_add.add_argument("--aspect-ratio", default=None,
                            help="W:H offered by the model (default: nearest the picture for a background, "
                                 "else the model's first)")
    assets_add.add_argument("--count", type=int, default=1, help=f"images to generate, 1 to {assets.MAX_COUNT}")
    assets_add.add_argument("--ref", action="append",
                            help="reference image (repeatable; a relative path is relative to the project folder)")
    assets_add.set_defaults(func=_cmd_assets_add)
    assets_list = assets_sub.add_parser("list", help="print the assets and the params each is sent with, as JSON")
    assets_list.add_argument("project", help="project folder")
    assets_list.set_defaults(func=_cmd_assets_list)
    assets_cost = assets_sub.add_parser("set-cost", help="record an asset's quoted cost")
    assets_cost.add_argument("project", help="project folder")
    assets_cost.add_argument("--asset", required=True)
    assets_cost.add_argument("--credits", required=True, type=float)
    assets_cost.set_defaults(func=_cmd_assets_set_cost)
    assets_next = assets_sub.add_parser("next", help="issue the batch's quoted assets through the ledger")
    assets_next.add_argument("project", help="project folder")
    assets_next.add_argument("--batch", required=True)
    assets_next.set_defaults(func=_cmd_assets_next)
    assets_done = assets_sub.add_parser("done", help="record a finished asset and store its images")
    assets_done.add_argument("project", help="project folder")
    assets_done.add_argument("--asset", required=True)
    assets_done.add_argument("--provider-id", required=True)
    assets_done.add_argument("--credits", required=True, type=float)
    assets_done.add_argument("--file", required=True, action="append", help="returned image (repeatable)")
    assets_done.set_defaults(func=_cmd_assets_done)
    assets_fail = assets_sub.add_parser("fail", help="record a failed asset")
    assets_fail.add_argument("project", help="project folder")
    assets_fail.add_argument("--asset", required=True)
    assets_fail.add_argument("--reason", required=True)
    assets_fail.add_argument("--credits", type=float, default=0.0)
    assets_fail.set_defaults(func=_cmd_assets_fail)
    assets_use = assets_sub.add_parser("use", help="make a generated image the element's swap in casting.json")
    assets_use.add_argument("project", help="project folder")
    assets_use.add_argument("--element", required=True)
    assets_use.add_argument("--image", required=True, help="one of the element's generated files, e.g. "
                                                           "assets/hero/a0001_1.png")
    assets_use.set_defaults(func=_cmd_assets_use)

    sound_p = sub.add_parser("sound", help="generated music, effects and ambience (sounds.json) and the mix")
    sound_sub = sound_p.add_subparsers(dest="sound_command")

    def placement(parser: argparse.ArgumentParser) -> None:
        start = parser.add_mutually_exclusive_group()
        start.add_argument("--at-frame", type=int, default=None, help="film frame where the sound starts")
        start.add_argument("--shot", type=int, default=None, help="start at the first frame of this shot")
        parser.add_argument("--offset-frames", type=int, default=None,
                            help="frames added to the first frame of --shot (may be negative)")

    sound_add = sound_sub.add_parser("add", help="plan an audio-generation job placed on a film frame")
    sound_add.add_argument("project", help="project folder")
    sound_add.add_argument("--kind", required=True, choices=sound.KINDS)
    sound_add.add_argument("--label", required=True, help="short name of the sound (any language but Chinese)")
    sound_add.add_argument("--prompt", required=True, help="English description of the sound itself")
    sound_add.add_argument("--model", required=True, help="audio model id from models.json")
    sound_add.add_argument("--seconds", required=True, type=float, help="duration asked from the model")
    sound_add.add_argument("--batch", required=True, help="approval batch of the sound")
    placement(sound_add)
    sound_add.add_argument("--gain-db", type=float, default=0.0, help="mix gain, -60 to 12 (default 0)")
    sound_add.add_argument("--fade-in", type=float, default=0.0, help="fade-in seconds (default 0)")
    sound_add.add_argument("--fade-out", type=float, default=sound.DEFAULT_FADE_OUT,
                           help=f"fade-out seconds (default {sound.DEFAULT_FADE_OUT})")
    sound_add.set_defaults(func=_cmd_sound_add)
    sound_list = sound_sub.add_parser("list", help="print the sounds, the params each is sent with and their "
                                                   "placement, as JSON")
    sound_list.add_argument("project", help="project folder")
    sound_list.set_defaults(func=_cmd_sound_list)
    sound_cost = sound_sub.add_parser("set-cost", help="record a sound's quoted cost")
    sound_cost.add_argument("project", help="project folder")
    sound_cost.add_argument("--sound", required=True)
    sound_cost.add_argument("--credits", required=True, type=float)
    sound_cost.set_defaults(func=_cmd_sound_set_cost)
    sound_next = sound_sub.add_parser("next", help="issue the batch's quoted sounds through the ledger")
    sound_next.add_argument("project", help="project folder")
    sound_next.add_argument("--batch", required=True)
    sound_next.set_defaults(func=_cmd_sound_next)
    sound_done = sound_sub.add_parser("done", help="record a finished sound and store its file")
    sound_done.add_argument("project", help="project folder")
    sound_done.add_argument("--sound", required=True)
    sound_done.add_argument("--provider-id", required=True)
    sound_done.add_argument("--credits", required=True, type=float)
    sound_done.add_argument("--file", required=True, help="returned audio file (.wav, .mp3, .m4a, .ogg, .flac)")
    sound_done.set_defaults(func=_cmd_sound_done)
    sound_fail = sound_sub.add_parser("fail", help="record a failed sound")
    sound_fail.add_argument("project", help="project folder")
    sound_fail.add_argument("--sound", required=True)
    sound_fail.add_argument("--reason", required=True)
    sound_fail.add_argument("--credits", type=float, default=0.0)
    sound_fail.set_defaults(func=_cmd_sound_fail)
    sound_set = sound_sub.add_parser("set", help="change a sound's start, gain, fades or mute (free, at any time)")
    sound_set.add_argument("project", help="project folder")
    sound_set.add_argument("--sound", required=True)
    placement(sound_set)
    sound_set.add_argument("--gain-db", type=float, default=None, help="mix gain, -60 to 12")
    sound_set.add_argument("--fade-in", type=float, default=None, help="fade-in seconds")
    sound_set.add_argument("--fade-out", type=float, default=None, help="fade-out seconds")
    muting = sound_set.add_mutually_exclusive_group()
    muting.add_argument("--mute", action="store_true", help="leave the sound out of the mix")
    muting.add_argument("--unmute", action="store_true", help="put a muted sound back into the mix")
    sound_set.set_defaults(func=_cmd_sound_set)
    sound_mix = sound_sub.add_parser("mix", help="mix the done, unmuted sounds into out/soundtrack.wav at -16 LUFS")
    sound_mix.add_argument("project", help="project folder")
    sound_mix.set_defaults(func=_cmd_sound_mix)

    extra_p = sub.add_parser("extra", help="extra shots that are not in the reference (extras.json); never "
                                           "part of assemble")
    extra_sub = extra_p.add_subparsers(dest="extra_command")
    extra_add = extra_sub.add_parser("add", help="plan a video job for a shot that is not in the reference")
    extra_add.add_argument("project", help="project folder")
    extra_add.add_argument("--label", required=True, help="short name of the shot (any language but Chinese)")
    extra_add.add_argument("--prompt", required=True, help="English description of the shot")
    extra_add.add_argument("--model", required=True, help="video model id from models.json")
    extra_add.add_argument("--seconds", required=True, type=int, help="whole seconds within the model's limits")
    extra_add.add_argument("--batch", required=True, help="approval batch of the extra")
    extra_add.add_argument("--extend", default=None, help="take to continue, U/T (e.g. u01/t001); needs --direction")
    extra_add.add_argument("--direction", choices=extras.DIRECTIONS, default=None,
                           help="backward: the new clip ends where the take begins; forward: it starts where the "
                                "take ends")
    extra_add.add_argument("--start", default=None, help="first frame of the new clip, U/T:FRAME (FRAME is first, "
                                                         "last or a frame number of the take's raw file)")
    extra_add.add_argument("--end", default=None, help="last frame of the new clip, U/T:FRAME")
    extra_add.add_argument("--ref", action="append", help="reference image under assets/ or swap/ (repeatable)")
    extra_add.add_argument("--quality", choices=plan.QUALITIES, default="draft",
                           help="draft: the model's first resolution, final: its last")
    extra_add.add_argument("--aspect-ratio", default=None,
                           help="W:H offered by the model (default: nearest the picture area)")
    extra_add.set_defaults(func=_cmd_extra_add)
    extra_list = extra_sub.add_parser("list", help="print the extras and the params and inputs each is sent with")
    extra_list.add_argument("project", help="project folder")
    extra_list.set_defaults(func=_cmd_extra_list)
    extra_cost = extra_sub.add_parser("set-cost", help="record an extra's quoted cost")
    extra_cost.add_argument("project", help="project folder")
    extra_cost.add_argument("--extra", required=True)
    extra_cost.add_argument("--credits", required=True, type=float)
    extra_cost.set_defaults(func=_cmd_extra_set_cost)
    extra_next = extra_sub.add_parser("next", help="issue the batch's quoted extras through the ledger")
    extra_next.add_argument("project", help="project folder")
    extra_next.add_argument("--batch", required=True)
    extra_next.set_defaults(func=_cmd_extra_next)
    extra_done = extra_sub.add_parser("done", help="record a finished extra and store its file")
    extra_done.add_argument("project", help="project folder")
    extra_done.add_argument("--extra", required=True)
    extra_done.add_argument("--provider-id", required=True)
    extra_done.add_argument("--credits", required=True, type=float)
    extra_done.add_argument("--file", required=True, help="returned video (.mp4, .mov, .mkv, .webm)")
    extra_done.set_defaults(func=_cmd_extra_done)
    extra_fail = extra_sub.add_parser("fail", help="record a failed extra")
    extra_fail.add_argument("project", help="project folder")
    extra_fail.add_argument("--extra", required=True)
    extra_fail.add_argument("--reason", required=True)
    extra_fail.add_argument("--credits", type=float, default=0.0)
    extra_fail.set_defaults(func=_cmd_extra_fail)

    approve = sub.add_parser("approve", help="record a credit approval for a batch")
    approve.add_argument("project", help="project folder")
    approve.add_argument("--batch", required=True)
    approve.add_argument("--limit", required=True, type=float, help="approved credits for the batch")
    approve.add_argument("--by", required=True, help="who approved")
    approve.add_argument("--note", default="")
    approve.set_defaults(func=_cmd_approve)

    budget = sub.add_parser("budget", help="the project budget in the ledger")
    budget_sub = budget.add_subparsers(dest="budget_command")
    budget_set = budget_sub.add_parser("set", help="record the project budget (replaces an earlier one); approve "
                                                   "then refuses to bring the sum of all batch limits above it")
    budget_set.add_argument("project", help="project folder")
    budget_set.add_argument("--limit", required=True, type=float, help="credits the user approved for the project")
    budget_set.add_argument("--by", required=True, help="who approved")
    budget_set.add_argument("--note", default="")
    budget_set.set_defaults(func=_cmd_budget_set)

    audit = sub.add_parser("audit", help="check the ledger; exit 2 when it is not ok")
    audit.add_argument("project", help="project folder")
    audit.set_defaults(func=_cmd_audit)

    takes_p = sub.add_parser("takes", help="takes of a unit")
    takes_sub = takes_p.add_subparsers(dest="takes_command")
    takes_select = takes_sub.add_parser("select", help="select a unit's take")
    takes_select.add_argument("project", help="project folder")
    takes_select.add_argument("--unit", required=True)
    takes_select.add_argument("--take", required=True)
    takes_select.set_defaults(func=_cmd_takes_select)
    takes_cuts = takes_sub.add_parser("cuts", help="print a take's frame count, hard cuts and held tail after the "
                                                   "frame-rate conversion conform makes, as JSON")
    takes_cuts.add_argument("project", help="project folder")
    takes_cuts.add_argument("--unit", required=True)
    takes_cuts.add_argument("--take", required=True)
    takes_cuts.set_defaults(func=_cmd_takes_cuts)
    takes_review = takes_sub.add_parser("review", help="write review.mp4 (reference left, take right) and review.jpg "
                                                       "(first, middle and last frames per shot) into a conformed "
                                                       "take's folder, for the user to look at")
    takes_review.add_argument("project", help="project folder")
    takes_review.add_argument("--unit", required=True)
    takes_review.add_argument("--take", required=True)
    takes_review.set_defaults(func=_cmd_takes_review)
    takes_confirm = takes_sub.add_parser("confirm", help="record that the user confirmed the unit's selected, "
                                                         "conformed take (assemble needs every unit confirmed)")
    takes_confirm.add_argument("project", help="project folder")
    takes_confirm.add_argument("--unit", required=True)
    takes_confirm.add_argument("--take", required=True)
    takes_confirm.add_argument("--by", required=True, help="the user who confirmed")
    takes_confirm.add_argument("--note", default="")
    takes_confirm.set_defaults(func=_cmd_takes_confirm)

    conform_p = sub.add_parser("conform", help="fit a take to the reference: one clip per shot, exact frame counts")
    conform_p.add_argument("project", help="project folder")
    conform_p.add_argument("--unit", required=True)
    conform_p.add_argument("--take", required=True)
    conform_p.add_argument("--start-frame", type=int, default=0,
                           help="take frame (after the frame-rate conversion) that becomes the unit's first frame")
    conform_p.add_argument("--allow-retime", action="store_true",
                           help="resample a take that is too short evenly to the unit's frame count")
    conform_p.add_argument("--take-cuts",
                           help="take frames (after the frame-rate conversion) where each shot of the unit starts, "
                                "then the frame after its last shot, e.g. 0,53,105; each segment is resampled to its "
                                "shot's frame count (not with --start-frame or --allow-retime)")
    conform_p.set_defaults(func=_cmd_conform)

    qc_p = sub.add_parser("qc", help="score a conformed take against the reference; write scores.json")
    qc_p.add_argument("project", help="project folder")
    qc_p.add_argument("--unit", required=True)
    qc_p.add_argument("--take", required=True)
    qc_p.set_defaults(func=_cmd_qc)

    assemble_p = sub.add_parser("assemble", help="join the confirmed selected takes into out/final.mp4 (and "
                                                 "out/master.mov) with the soundtrack when one is mixed")
    assemble_p.add_argument("project", help="project folder")
    sound_choice = assemble_p.add_mutually_exclusive_group()
    sound_choice.add_argument("--audio", help="audio file to put in the film instead of the soundtrack (the reference "
                                              "audio is never used)")
    sound_choice.add_argument("--silent", action="store_true", help="make the film without sound")
    assemble_p.add_argument("--preview", action="store_true",
                            help="write only out/preview.mp4 from the selected takes, without needing the user's "
                                 "confirmations (not with --audio)")
    assemble_p.set_defaults(func=_cmd_assemble)

    compare_p = sub.add_parser("compare", help="write out/compare.mp4: reference and result side by side")
    compare_p.add_argument("project", help="project folder")
    compare_p.set_defaults(func=_cmd_compare)

    report_p = sub.add_parser("report", help="write out/report.md (Korean)")
    report_p.add_argument("project", help="project folder")
    report_p.set_defaults(func=_cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        return func(args)
    except CineRecastError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # last resort: one line instead of a traceback, and an exit code of its own
        message = " ".join(str(exc).split())
        print(f"error: unexpected failure ({type(exc).__name__}: {message}); this is a defect in cine-recast",
              file=sys.stderr)
        if os.environ.get("CINE_RECAST_DEBUG") == "1":
            traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
