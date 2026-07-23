"""Command-line entry point.

    python -m smart_ads --input input/Axis.psd --output output
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import FORMATS_BY_NAME
from .pipeline import run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart_ads",
        description="Transform a master creative (PSD) into IAB ad assets.")
    parser.add_argument("--input", "-i", required=True, help="Master PSD (or image) path")
    parser.add_argument("--output", "-o", default="output", help="Output directory")
    parser.add_argument("--formats", "-f", nargs="+", metavar="NAME",
                        choices=list(FORMATS_BY_NAME),
                        help="Subset of formats to render (default: all)")
    parser.add_argument("--jpg", action="store_true", help="Save JPG instead of PNG")
    parser.add_argument("--debug", action="store_true",
                        help="Also write a classification overlay")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s")

    formats = [FORMATS_BY_NAME[n] for n in args.formats] if args.formats else None
    written = run(args.input, args.output, formats=formats,
                  debug=args.debug, jpg=args.jpg)
    print(f"\nGenerated {len(written)} asset(s) in {args.output}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
