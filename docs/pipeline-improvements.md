# Pipeline improvement suggestions (PDF → Markdown → EPUB)

Status: written 2026-07-05 after a review of the pipeline against EPUB 3.3 best
practices and EPUB Accessibility 1.1 (WCAG). Items marked ✅ were implemented
during that review; items marked ✅ (2nd pass) were implemented later the same
day on the `pipeline-improvements` branch, with tests in
`tests/test_pipeline.py`. Remaining open items are listed at the end.

## Implemented in this review ✅

- `lang`/`xml:lang` on package, chapters, nav, NCX and title page now come from
  `dc:language` (was hardcoded `"en"` — a WCAG 3.1.1 failure for German books).
- Schema.org accessibility metadata (accessMode, accessModeSufficient,
  accessibilityFeature, accessibilityHazard, accessibilitySummary) emitted with
  honest defaults, overridable via an `"accessibility"` object in
  description.json.
- Landmarks nav (cover / toc / start-of-content) in TOC.xhtml; nav doc added to
  the spine; `"bodymatter": true` chapter flag selects the start-of-content
  target.
- Optional per-chapter `"title"` in description.json drives TOC/NCX labels and
  the chapter `<title>` — enables ASCII-slug filenames → zero PKG-010 warnings.
- python-markdown footnotes get `epub:type="noteref"/"footnote"` +
  DPUB-ARIA roles → popup footnotes in readers, proper screen-reader announce.
- Chapters containing MathML automatically get `properties="mathml"` in the
  manifest (fixes epubcheck OPF-014).
- Title page shows the cover image (with alt text) when `cover_image` is set;
  no more 100vh flexbox (poorly supported in reading systems).
- Duplicate CSS `<link>` when chapter css == default css fixed; build now
  prints a note for every image with empty alt text.

## Second pass ✅ (2nd pass, 2026-07-05)

1. ✅ **Seed description.json in pdf2md** — `pdf2md.seed_description()` writes a
   pre-filled description.json (title/author from PDF metadata via pypdfium2,
   `dc:language` from `--langs`); never overwrites an existing one.

2. ✅ **Alt-text drafting step** — `modules/alt_text.py` sends each empty-alt
   image plus surrounding paragraphs to Claude (`--dry-run` to list only;
   default model `claude-opus-4-8`, override with `--model`). Drafts are
   written into the markdown for human review. *Live API path still needs a
   one-time verification run with credentials.*

3. ✅ **Page-list navigation** — chapters containing
   `<span epub:type="pagebreak" role="doc-pagebreak" id="pageN" aria-label="N"></span>`
   produce a `<nav epub:type="page-list">`, the `pageBreakMarkers`
   accessibilityFeature, and `pageBreakSource` (from `dc:source`). The cleanup
   skill can now convert marker's `page-N-0` anchors instead of deleting them.

4. ✅ **Hierarchical TOC** — `##` headings become a nested level in the nav doc
   (ids from the markdown `toc` extension); `"toc_depth": 1` disables.

5. ✅ **marker 1.x options in main.py** — `--use-llm`, `--llm-service`,
   `--paginate`, `--no-images`, `--langs` (dc:language seeding only — marker
   1.x no longer takes language hints), `--yes` forwarded to mark2epub.

6. ✅ **README refresh.**

7. ✅ **Non-interactive mode for mark2epub** — `--yes`/`-y` flag; build.sh uses
   it instead of piping blank lines.

8. ✅ **DAISY Ace in build** — the skill's build.sh runs Ace when installed.

9. ✅ **GIF handling** — non-JPEG/PNG images are now passed through unchanged
   (the old code attempted a rename-to-.jpg conversion whose result was never
   used; the image was bundled unoptimized via the fallback path with a
   spurious error). WebP/SVG remain unsupported (not EPUB core media types).

10. ✅ **Dead code** — `update_package_manifest()` removed.

11. ✅ **`dc:date` format** — non-W3CDTF values now print a warning.

12. ✅ **Committed on branch `pipeline-improvements`.**

## Still open

- **Live-run verification of alt_text.py** with real credentials (one book).
- **marker `use_llm` evaluation** — try `--use-llm --llm-service
  marker.services.claude.ClaudeService` on the next book and compare cleanup
  effort against the plain run.
- **WebP/SVG support** in mark2epub (convert to PNG/JPEG at bundle time and
  rewrite refs), if such sources ever appear.
- **Per-page-anchor retention in the cleanup skill** — the skill's
  clean_template still deletes `page-N-0` anchors; a PAGELIST option could
  convert them to pagebreak spans (printed page number = PDF page + offset).
