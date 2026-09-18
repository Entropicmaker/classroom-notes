#!/usr/bin/env python3
"""Render a PPT/PPTX to one PNG per slide using LibreOffice and Poppler."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_tool(name, env_name, extra=()):
    configured = os.environ.get(env_name)
    candidates = [configured, shutil.which(name), *extra]
    runtime_glob = Path.home() / ".cache/codex-runtimes"
    if runtime_glob.is_dir():
        candidates.extend(
            str(p)
            for p in sorted(runtime_glob.glob(f"*/dependencies/bin/override/{name}"))
        )
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise FileNotFoundError(
        f"Cannot find {name}. Set {env_name} to its executable path."
    )


def run(command, timeout, input_text=None):
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command[0]}\n{detail[-4000:]}"
        )
    return result


def convert_with_powerpoint(source, pdf, timeout):
    app = Path("/Applications/Microsoft PowerPoint.app")
    osascript = shutil.which("osascript")
    if platform.system() != "Darwin" or not app.is_dir() or not osascript:
        raise FileNotFoundError("Microsoft PowerPoint AppleScript backend is unavailable")
    script = r'''
on run argv
  set sourcePath to item 1 of argv
  set outputPath to item 2 of argv
  tell application "Microsoft PowerPoint"
    try
      open POSIX file sourcePath
      set deck to active presentation
      save deck in (POSIX file outputPath) as save as PDF
      close deck saving no
    on error errorMessage number errorNumber
      try
        close active presentation saving no
      end try
      error errorMessage number errorNumber
    end try
  end tell
end run
'''
    run([osascript, "-", str(source), str(pdf)], timeout, script)
    if not pdf.is_file():
        raise RuntimeError("PowerPoint reported success but produced no PDF")
    return "powerpoint", str(app)


def convert_with_libreoffice(source, out, timeout):
    soffice = find_tool(
        "soffice",
        "CLASSROOM_SOFFICE",
        (
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/opt/homebrew/bin/soffice",
            "/usr/local/bin/soffice",
        ),
    )
    with tempfile.TemporaryDirectory(prefix="classroom-lo-") as profile:
        run(
            [
                soffice,
                "--headless",
                f"-env:UserInstallation={Path(profile).resolve().as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out),
                str(source),
            ],
            timeout,
        )
    pdf_candidates = list(out.glob("*.pdf"))
    if len(pdf_candidates) != 1:
        raise RuntimeError(
            f"Expected one rendered PDF, found {len(pdf_candidates)} in {out}"
        )
    return "libreoffice", soffice, pdf_candidates[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--backend",
        choices=("auto", "powerpoint", "libreoffice"),
        default="auto",
        help="auto prefers Microsoft PowerPoint on macOS and falls back to LibreOffice",
    )
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source missing: {source}")
    if source.suffix.lower() not in {".ppt", ".pptx"}:
        raise ValueError("Source must be .ppt or .pptx")
    if args.dpi < 72 or args.dpi > 300:
        raise ValueError("--dpi must be between 72 and 300")

    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Output already populated; choose a new work directory")

    pdftoppm = find_tool(
        "pdftoppm",
        "CLASSROOM_PDFTOPPM",
        ("/opt/homebrew/bin/pdftoppm", "/usr/local/bin/pdftoppm"),
    )

    warnings = []
    backend_tool = None
    pdf = out / f"{source.stem}.pdf"
    if args.backend in {"auto", "powerpoint"}:
        try:
            backend, backend_tool = convert_with_powerpoint(source, pdf, args.timeout)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            if args.backend == "powerpoint":
                raise
            warnings.append(f"PowerPoint backend failed; used LibreOffice fallback: {error}")
            if pdf.exists():
                pdf.unlink()
            backend, backend_tool, pdf = convert_with_libreoffice(
                source, out, args.timeout
            )
    else:
        backend, backend_tool, pdf = convert_with_libreoffice(source, out, args.timeout)
    prefix = out / "raw-page"
    run(
        [pdftoppm, "-png", "-r", str(args.dpi), str(pdf), str(prefix)],
        args.timeout,
    )

    raw_pages = sorted(
        out.glob("raw-page-*.png"),
        key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1)),
    )
    if not raw_pages:
        raise RuntimeError("Renderer produced no PNG pages")

    pages = []
    for number, raw in enumerate(raw_pages, 1):
        destination = out / f"page-{number:03}.png"
        raw.rename(destination)
        data = destination.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"Invalid PNG output: {destination}")
        pages.append(
            {
                "number": number,
                "path": destination.name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    manifest = {
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "pdf": pdf.name,
        "dpi": args.dpi,
        "backend": backend,
        "slide_count": len(pages),
        "pages": pages,
        "tools": {backend: backend_tool, "pdftoppm": pdftoppm},
        "warnings": warnings,
    }
    manifest_path = out / "render-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "rendered",
                "backend": backend,
                "slides": len(pages),
                "manifest": str(manifest_path),
                "first_page": str(out / pages[0]["path"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error))
