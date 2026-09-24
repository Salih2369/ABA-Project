#!/usr/bin/env python
"""Build the reviewed project-code ZIP used by demo_colab.ipynb."""

from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Sequence


BUNDLE_FILES = (
    "aba_demo/__init__.py",
    "aba_demo/engine.py",
    "aba_demo/export.py",
    'aba_demo/vision.py',
    'aba_demo/botsort_reid.yaml',
    'aba_demo/context_schema.py',
    'aba_demo/openrouter_context.py',
    'requirements-demo-vision.txt',
    "tests/test_demo_vision.py",
    "aba_demo/colab_workflow.py",
    "tests/test_colab_workflow.py",
    "tests/test_context_schema.py",
    "tests/test_openrouter_context.py",
)
DEFAULT_OUTPUT = "demo_colab_bundle.zip"


def create_bundle(project_root: Path, output: Path, *, force: bool = False) -> None:
    """Write the notebook's reviewed source files to *output*."""
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output} (use --force to replace it)")

    missing = [
        relative
        for relative in BUNDLE_FILES
        if not (project_root / relative).is_file()
    ]
    if missing:
        raise FileNotFoundError("Missing required bundle files: " + ", ".join(missing))

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for relative in BUNDLE_FILES:
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info,
                    (project_root / relative).read_bytes(),
                    compresslevel=9,
                )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="replace an existing output ZIP")
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    try:
        create_bundle(project_root, args.output.resolve(), force=args.force)
    except (FileExistsError, FileNotFoundError) as error:
        parser.error(str(error))
    print(f"Created {args.output} with {len(BUNDLE_FILES)} reviewed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
