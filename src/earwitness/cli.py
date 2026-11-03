"""Command-line entry point. M0 version: every subcommand is a stub."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from earwitness import __version__

NOT_IMPLEMENTED = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with the subcommands from docs/HANDOFF.md."""
    parser = argparse.ArgumentParser(
        prog="earwitness",
        description="Geolocate a recording from background thunder or prop-aircraft sound.",
    )
    parser.add_argument("--version", action="version", version=f"earwitness {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    data = sub.add_parser("data", help="download public data into the local cache (M2, M3)")
    data_sub = data.add_subparsers(dest="source", metavar="<source>")
    for name, text in (("glm", "GOES GLM lightning (M2)"), ("adsb", "adsb.lol traces (M3)")):
        p = data_sub.add_parser(name, help=text)
        p.add_argument("--date", required=True, help="UTC date, YYYY-MM-DD")
        p.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
        p.set_defaults(func=_not_implemented, milestone="M2" if name == "glm" else "M3")

    sim = sub.add_parser("simulate", help="write a synthetic recording plus truth.json (M1)")
    sim_sub = sim.add_subparsers(dest="method", metavar="<method>")
    for name in ("thunder", "aircraft"):
        p = sim_sub.add_parser(name, help=f"synthetic {name} case")
        p.set_defaults(func=_not_implemented, milestone="M1")

    ana = sub.add_parser("analyze", help="locate a recording (M2 thunder, M4 aircraft)")
    ana_sub = ana.add_subparsers(dest="method", metavar="<method>")
    for name, ms in (("thunder", "M2"), ("aircraft", "M4")):
        p = ana_sub.add_parser(name, help=f"{name} method ({ms})")
        p.add_argument("file", help="local audio or video file")
        p.set_defaults(func=_not_implemented, milestone=ms)

    audit = sub.add_parser("audit", help="report which location clues a recording contains")
    audit.add_argument("file", help="local audio or video file")
    audit.set_defaults(func=_not_implemented, milestone="M5")

    ui = sub.add_parser("ui", help="open the desktop UI (M5)")
    ui.set_defaults(func=_not_implemented, milestone="M5")
    return parser


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"earwitness: not implemented yet (planned for {args.milestone})", file=sys.stderr)
    return NOT_IMPLEMENTED


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
