import argparse
from asset_transformer import AssetTransformer

def main():
    parser = argparse.ArgumentParser(
        description="Asset Transformation Tool"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to master PSD or JPEG"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory"
    )

    args = parser.parse_args()

    transformer = AssetTransformer(
        args.input,
        args.output
    )
    transformer.run()

if __name__ == "__main__":
    main()
