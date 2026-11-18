import argparse
import json
import sys
from pathlib import Path

from . import __version__, conform, ledger, plan, takes
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


def _cmd_analyze(args: argparse.Namespace) -> int:
    cuts = _parse_cuts(args.cuts) if args.cuts is not None else None
    result = analyze(Path(args.project), cuts=cuts)
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
                      quality=args.quality, max_unit_seconds=args.max_unit_seconds, replace=args.replace)
    for unit in data["units"]:
        print(f"{unit['id']}: shots {unit['shots']}  frames {unit['start']}-{unit['end']}  "
              f"duration {unit['params']['duration']} s")
    print(f"planned jobs: {', '.join(job['id'] for job in data['jobs'])} (batch {args.batch})")
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


def _cmd_approve(args: argparse.Namespace) -> int:
    root = Path(args.project)
    load_project(root)  # never start a ledger outside a project folder
    record = ledger.approve(root, args.batch, args.limit, args.by, args.note)
    print(f"approved batch {record['batch']}: limit {record['limit_credits']} credits (by {record['by']})")
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


def _cmd_conform(args: argparse.Namespace) -> int:
    paths = conform.conform(Path(args.project), args.unit, args.take, start_frame=args.start_frame,
                            allow_retime=args.allow_retime)
    for path in paths:
        print(path)
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
    plan_build.add_argument("--replace", action="store_true",
                            help="rebuild an existing plan (only while no job has been issued)")
    plan_build.set_defaults(func=_cmd_plan_build)
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

    approve = sub.add_parser("approve", help="record a credit approval for a batch")
    approve.add_argument("project", help="project folder")
    approve.add_argument("--batch", required=True)
    approve.add_argument("--limit", required=True, type=float, help="approved credits for the batch")
    approve.add_argument("--by", required=True, help="who approved")
    approve.add_argument("--note", default="")
    approve.set_defaults(func=_cmd_approve)

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

    conform_p = sub.add_parser("conform", help="fit a take to the reference: one clip per shot, exact frame counts")
    conform_p.add_argument("project", help="project folder")
    conform_p.add_argument("--unit", required=True)
    conform_p.add_argument("--take", required=True)
    conform_p.add_argument("--start-frame", type=int, default=0,
                           help="take frame (after the frame-rate conversion) that becomes the unit's first frame")
    conform_p.add_argument("--allow-retime", action="store_true",
                           help="resample a take that is too short evenly to the unit's frame count")
    conform_p.set_defaults(func=_cmd_conform)
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


if __name__ == "__main__":
    sys.exit(main())
