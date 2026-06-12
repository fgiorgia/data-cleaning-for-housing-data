"""Re-encode a UTF-8 file, stripping a leading byte-order mark if present."""

from __future__ import annotations

import argparse
import shutil


def transcode_no_bom(input_path: str, output_path: str) -> None:
    print("Loading and re-encoding...")
    # 'utf-8-sig' transparently drops the BOM on read; copyfileobj streams
    # in chunks so arbitrarily large files never need to fit in memory.
    # newline='' preserves the original line endings byte-for-byte.
    with (
        open(input_path, mode="r", encoding="utf-8-sig", newline="") as source,
        open(output_path, mode="w", encoding="utf-8", newline="") as destination,
    ):
        shutil.copyfileobj(source, destination, length=1024 * 1024)
    print("Done")


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Copy a UTF-8 file, removing the BOM if present."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args: argparse.Namespace = parser.parse_args()
    transcode_no_bom(args.input, args.output)


if __name__ == "__main__":
    main()