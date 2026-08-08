from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    return sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))


def main() -> None:
    failures: list[str] = []
    for source in markdown_files():
        for raw_target in LINK.findall(source.read_text(encoding="utf-8")):
            target = raw_target.split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:", "<")):
                continue
            if not (source.parent / target).exists():
                failures.append(f"{source.relative_to(ROOT)}: missing {target}")
    if failures:
        raise SystemExit("Broken local Markdown links:\n" + "\n".join(failures))
    print(f"Checked local links in {len(markdown_files())} Markdown files.")


if __name__ == "__main__":
    main()
