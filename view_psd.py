#!/usr/bin/env python3
"""
View a .psd file as images.

- Renders the flattened composite (what the file looks like as a whole)
- Optionally exports each layer as its own PNG

Usage:
    python view_psd.py path/to/file.psd
    python view_psd.py path/to/file.psd --layers          # also export each layer
    python view_psd.py path/to/file.psd --out ./exported   # custom output folder

Requires:
    pip install psd-tools pillow
"""

import argparse
import sys
from pathlib import Path

from psd_tools import PSDImage


def safe_name(name: str, fallback: str) -> str:
    name = (name or fallback).strip()
    keep = "-_.() " + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    cleaned = "".join(c if c in keep else "_" for c in name)
    return cleaned or fallback


def export_layers(psd, out_dir: Path):
    """Flatten and save every layer (including nested ones inside groups)."""
    count = 0
    for i, layer in enumerate(psd.descendants()):
        if layer.is_group():
            continue
        try:
            img = layer.composite()
        except Exception as e:
            print(f"  skipped '{layer.name}': {e}")
            continue
        if img is None:
            continue
        fname = f"{i:03d}_{safe_name(layer.name, 'layer')}.png"
        img.save(out_dir / fname)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="View/export a PSD file as PNG images.")
    parser.add_argument("psd_path", type=Path, help="Path to the .psd (or .psb) file")
    parser.add_argument("--layers", action="store_true", help="Also export each layer as a separate PNG")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: alongside the PSD)")
    args = parser.parse_args()

    if not args.psd_path.exists():
        sys.exit(f"File not found: {args.psd_path}")

    out_dir = args.out or args.psd_path.with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening {args.psd_path} ...")
    psd = PSDImage.open(args.psd_path)
    print(f"Size: {psd.width}x{psd.height}, {len(list(psd))} top-level layers")

    # Full flattened composite (what you'd see as a normal image)
    composite = psd.composite()
    composite_path = out_dir / f"{args.psd_path.stem}_composite.png"
    composite.save(composite_path)
    print(f"Saved composite -> {composite_path}")

    if args.layers:
        print("Exporting individual layers...")
        n = export_layers(psd, out_dir)
        print(f"Saved {n} layer PNGs -> {out_dir}/")

    print("Done.")


if __name__ == "__main__":
    main()