"""A stand-in Tesseract program, so OCR plumbing is tested without the real one."""

import stat
from pathlib import Path


def write_fake_tesseract(directory: Path, ocr_behaviour: str, langs: str = "eng\nhin") -> Path:
    """Write a stand-in ``tesseract`` script into ``directory``.

    It answers ``--version`` and ``--list-langs`` (listing ``langs``) like the real
    program; for an OCR call it runs ``ocr_behaviour``, shell code in which "$2"
    is the output base path.
    """
    script = directory / "tesseract"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  --version) echo "tesseract 0.0.0-fake";;\n'
        f"  --list-langs) printf 'List of available languages (2):\\n{langs}\\n';;\n"
        f"  *) {ocr_behaviour};;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


FAKE_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)
