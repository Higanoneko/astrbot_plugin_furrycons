"""Validate and package a theme folder (for example themes/material)."""

import argparse
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from furrycons.themes import load_theme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    source = options.folder.resolve()
    theme = load_theme(source, "local")
    if options.output.resolve().is_relative_to(source):
        raise SystemExit("Output ZIP must be outside the source theme folder")
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(options.output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    print(f"{theme.key}: {options.output}")


if __name__ == "__main__":
    main()
