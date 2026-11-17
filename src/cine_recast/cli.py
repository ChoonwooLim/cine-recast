import argparse
import sys
from pathlib import Path

from . import __version__
from .errors import CineRecastError


def _cmd_analyze(args: argparse.Namespace) -> int:
    project = Path(args.project)
    if not project.is_dir():
        raise CineRecastError(f"project folder not found: {project}")
    raise CineRecastError("analyze is not implemented yet")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cine-recast")
    parser.add_argument("--version", action="version", version=f"cine-recast {__version__}")
    sub = parser.add_subparsers(dest="command")
    analyze = sub.add_parser("analyze", help="analyze the reference video of a project")
    analyze.add_argument("project", help="project folder")
    analyze.set_defaults(func=_cmd_analyze)
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
