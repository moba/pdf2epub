#!/usr/bin/env python3
"""Draft alt text for images in pdf2epub markdown using Claude vision.

Scans a book work dir for image refs with EMPTY alt text, sends each image
plus its surrounding paragraphs to Claude, and writes the drafted alt text
back into the markdown. Drafts are meant for human review — read them before
building the EPUB (schema:accessibilityFeature "alternativeText" should only
be claimed for reviewed alt text).

Usage:
    python3 -m modules.alt_text <work_dir> [--language de] [--model MODEL]
                                [--dry-run]

Auth: the anthropic SDK resolves credentials from the environment
(ANTHROPIC_API_KEY or an `ant auth login` profile).
"""
import argparse
import base64
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-opus-4-8"
IMAGE_REF = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
MAX_EDGE = 1024  # downscale before upload; alt text needs no more detail


@dataclass
class MissingAlt:
    path: str      # image path as written in the markdown
    start: int     # span of the whole ![](...) ref in the source text
    end: int
    context: str   # surrounding paragraphs


def find_missing_alt(md_text: str) -> list[MissingAlt]:
    """Image refs with empty alt text, excluding images/unused/."""
    paragraphs = md_text.split("\n\n")
    found = []
    for m in IMAGE_REF.finditer(md_text):
        alt, path = m.group(1), m.group(2).strip()
        if alt.strip() or "unused/" in path:
            continue
        # context: the paragraph before and after the one containing the ref
        pos = 0
        idx = 0
        for i, p in enumerate(paragraphs):
            if pos <= m.start() < pos + len(p) + 2:
                idx = i
                break
            pos += len(p) + 2
        around = paragraphs[max(0, idx - 1):idx + 2]
        context = "\n\n".join(p for p in around if not p.strip().startswith("!["))
        found.append(MissingAlt(path=path, start=m.start(), end=m.end(),
                                context=context.strip()))
    return found


def apply_alt(md_text: str, missing: MissingAlt, alt: str) -> str:
    """Write the drafted alt text into the ref at missing's span."""
    alt = alt.strip().replace("]", ")").replace("[", "(")
    return (md_text[:missing.start]
            + f"![{alt}]({missing.path})"
            + md_text[missing.end:])


def encode_image(image_path: Path) -> tuple[str, str]:
    """(base64 data, media type), downscaled for token economy."""
    from PIL import Image
    with Image.open(image_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        ratio = min(MAX_EDGE / max(img.size), 1.0)
        if ratio < 1.0:
            img = img.resize((int(img.width * ratio), int(img.height * ratio)),
                             Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"


def draft_alt(client, model: str, image_path: Path, context: str,
              language: str, book_title: str) -> str:
    data, media_type = encode_image(image_path)
    prompt = (
        f"This figure appears in the book “{book_title}”. "
        f"Surrounding text:\n\n{context}\n\n"
        f"Write alt text for the figure in {language}: one short sentence "
        "describing what the figure shows, for a reader who cannot see it. "
        "No introduction, no quotes — reply with the alt text only."
    )
    response = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type,
                            "data": data}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return " ".join(text.split())


def process_workdir(work_dir: Path, model: str = DEFAULT_MODEL,
                    language: str = None, dry_run: bool = False) -> int:
    """Draft alt text for every empty-alt image ref in the work dir's
    markdown files. Returns the number of drafts written."""
    desc_path = work_dir / "description.json"
    book_title, desc_lang = "", None
    if desc_path.exists():
        meta = json.loads(desc_path.read_text(encoding="utf-8")).get("metadata", {})
        book_title = meta.get("dc:title", "")
        desc_lang = meta.get("dc:language")
    language = language or desc_lang or "en"

    md_files = sorted(p for p in work_dir.glob("*.md"))
    targets = []  # (md_path, MissingAlt)
    for md_path in md_files:
        for missing in find_missing_alt(md_path.read_text(encoding="utf-8")):
            image_path = work_dir / "images" / Path(missing.path).name
            if image_path.exists():
                targets.append((md_path, missing, image_path))
            else:
                print(f"  skip (image not found): {missing.path} in {md_path.name}")

    if not targets:
        print("No images with missing alt text found.")
        return 0
    if dry_run:
        for md_path, missing, _ in targets:
            print(f"{md_path.name}: {missing.path}")
        print(f"{len(targets)} image(s) need alt text (dry run — nothing sent).")
        return 0

    import anthropic
    client = anthropic.Anthropic()
    if not (client.api_key or client.auth_token):
        sys.exit("No Anthropic credentials found — set ANTHROPIC_API_KEY "
                 "(or run with --dry-run to just list the images).")

    written = 0
    for md_path in dict.fromkeys(t[0] for t in targets):
        text = md_path.read_text(encoding="utf-8")
        # re-scan so spans stay valid as we rewrite front to back
        for missing in reversed(find_missing_alt(text)):
            image_path = work_dir / "images" / Path(missing.path).name
            if not image_path.exists():
                continue
            try:
                alt = draft_alt(client, model, image_path, missing.context,
                                language, book_title)
            except anthropic.APIStatusError as e:
                print(f"  API error for {missing.path}: {e.status_code} {e.message}")
                continue
            except anthropic.APIConnectionError:
                print("  network error — aborting")
                return written
            text = apply_alt(text, missing, alt)
            written += 1
            print(f"{md_path.name}: {missing.path}\n  -> {alt}")
        md_path.write_text(text, encoding="utf-8")

    print(f"\n{written} alt text draft(s) written — review them before building "
          "the EPUB, then add \"alternativeText\" to accessibilityFeature.")
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("--language", default=None,
                        help="Alt text language (default: dc:language from description.json)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="List images needing alt text without calling the API")
    args = parser.parse_args()
    if not args.work_dir.is_dir():
        sys.exit(f"not a directory: {args.work_dir}")
    process_workdir(args.work_dir, model=args.model, language=args.language,
                    dry_run=args.dry_run)


if __name__ == "__main__":
    main()
