#!/usr/bin/env python3
"""Check the local dependencies and vault configuration used by this skill."""

import json
import platform
import shutil
import sys
from pathlib import Path

from render_pptx import find_tool


def main():
    skill = Path(__file__).resolve().parents[1]
    config_path = skill / "references" / "vault.json"
    errors = []
    warnings = []
    tools = {}

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        config = {}
        errors.append(f"Cannot read vault config: {error}")

    vault = Path(config.get("vault", "")).expanduser()
    courses = vault / config.get("courses_dir", "")
    attachments = vault / config.get("attachments_dir", "")
    if not vault.is_dir():
        errors.append(f"Vault not found: {vault}")
    if not courses.is_dir():
        errors.append(f"Courses directory not found: {courses}")
    if not attachments.is_dir():
        warnings.append(
            f"Attachments directory does not exist yet; commit can create it: {attachments}"
        )

    powerpoint = Path("/Applications/Microsoft PowerPoint.app")
    if powerpoint.is_dir():
        tools["powerpoint"] = str(powerpoint)

    for name, env_name in (("pdftoppm", "CLASSROOM_PDFTOPPM"),):
        try:
            tools[name] = find_tool(name, env_name)
        except FileNotFoundError as error:
            warnings.append(str(error) + " PPT/PDF visual review will be unavailable.")

    try:
        tools["soffice"] = find_tool("soffice", "CLASSROOM_SOFFICE")
    except FileNotFoundError as error:
        if "powerpoint" not in tools:
            warnings.append(str(error) + " PPT visual review will be unavailable.")

    vision_script = skill / "scripts" / "vision_ocr.swift"
    swift = shutil.which("swift")
    if platform.system() == "Darwin" and swift and vision_script.is_file():
        tools["vision_ocr"] = f"{swift} {vision_script}"
    else:
        warnings.append(
            "macOS Vision OCR fallback is unavailable; use an image-capable model for image sources."
        )

    for required in (
        "extract_pptx.py",
        "render_pptx.py",
        "vault_import.py",
        "vision_ocr.swift",
    ):
        path = skill / "scripts" / required
        if not path.is_file():
            errors.append(f"Missing skill script: {path}")

    audio_cli = Path.home() / '.dsh' / 'vendor' / 'dsh-classroom-audio' / 'bin' / 'audio.mjs'
    if audio_cli.is_file() and shutil.which('node'):
        tools['classroom_audio_cli'] = str(audio_cli)
    else:
        warnings.append('Optional recording transcription plugin is unavailable; PPT-only work remains supported.')

    result = {
        "status": "error" if errors else "ready" if not warnings else "ready_with_warnings",
        "python": sys.version.split()[0],
        "skill": str(skill),
        "vault": str(vault),
        "courses_dir": str(courses),
        "tools": tools,
        "errors": errors,
        "warnings": warnings,
        "audio_check": 'Run classroom_audio action=doctor to check the audio engine and key availability without uploading recordings.',
        "dsh_runtime_note": (
            "Prefer read_image with an image-capable model. On macOS, vision_ocr is a "
            "text-only fallback and may require a one-time DSH permission approval."
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
