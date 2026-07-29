import argparse
import os

from . import psd_source
from .pipeline import run


def main():
    p = argparse.ArgumentParser(
        prog="adapt",
        description="Transform a square master ad (PSD/JPEG) into exact-sized "
                    "secondary assets using CV-driven cropping and re-layout.")
    p.add_argument("-i", "--input", default="input/Axis.psd",
                   help="master asset (.psd / .jpg / .png)")
    p.add_argument("-o", "--output", default="output",
                   help="output directory")
    p.add_argument("--no-debug", action="store_true",
                   help="skip debug renders and montage")
    p.add_argument("--use-layout", action="store_true",
                   help="apply layouts hand-edited in the web editor "
                        "(output/layouts/<name>.json) instead of the algorithmic ones")
    p.add_argument("--quiet", action="store_true",
                   help="silence the step-by-step progress log (or set ADAPT_QUIET=1)")
    p.add_argument("--max-dim", type=int, default=None,
                   help=f"longest side kept for layout work (default "
                        f"{psd_source.MAX_WORK_DIM}); lower it if a very large "
                        f"master strains memory")
    p.add_argument("--serve", action="store_true",
                   help="start the web layout editor instead of rendering")
    p.add_argument("--port", type=int, default=8000, help="port for --serve")
    p.add_argument("--host", default="127.0.0.1", help="interface for --serve")
    args = p.parse_args()

    if args.quiet:
        from . import log
        log.quiet(True)
    if args.max_dim:
        psd_source.MAX_WORK_DIM = args.max_dim

    if args.serve:
        from .web import serve
        input_dir = args.input if os.path.isdir(args.input) else os.path.dirname(
            args.input) or "input"
        serve(input_dir, args.output, host=args.host, port=args.port)
        return

    print(f"adapt: {args.input} -> {args.output}/")
    run(args.input, args.output, debug=not args.no_debug, use_saved=args.use_layout)
    print("done.")


if __name__ == "__main__":
    main()
