import argparse
import sys
from pathlib import Path

from . import __version__
from .analyze import analyze, review_strips
from .errors import CineRecastError
from .project import RIGHT_ANSWERS, RIGHTS_ITEMS, init_project, set_right


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
