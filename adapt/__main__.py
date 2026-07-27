import argparse

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
    args = p.parse_args()

    print(f"adapt: {args.input} -> {args.output}/")
    run(args.input, args.output, debug=not args.no_debug)
    print("done.")


if __name__ == "__main__":
    main()
