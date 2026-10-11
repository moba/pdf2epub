# PDF2EPUB 📚

Convert PDF files to nicely structured Markdown and accessible EPUB 3 with
intelligent layout detection.

> **This is a fork** of [overcuriousity/pdf2epub](https://github.com/overcuriousity/pdf2epub)
> (MIT), itself built on [AlexPof/mark2epub](https://github.com/AlexPof/mark2epub).
> The fork reworks the EPUB side for EPUB 3.3 / EPUB Accessibility 1.1:
> language tags from `dc:language` instead of a hardcoded `"en"`, schema.org
> accessibility metadata, a landmarks nav, hierarchical TOC/NCX groups
> (`chapter.group`), per-chapter `xml:lang`, page-list navigation from
> `pagebreak` spans, popup footnote semantics, cross-chapter link rewriting,
> configurable image compression, a non-interactive `--yes` mode, and an
> alt-text drafting pass. See `docs/pipeline-improvements.md` for what was
> changed and what is still open.
>
> Companion repository: [pdf2epub-cleanup-skill](https://github.com/moba/pdf2epub-cleanup-skill)
> — the Claude Code skill that drives this pipeline and cleans up its markdown
> before the EPUB build.

## ✨ Features

- 📖 Smart layout detection for books and academic papers (marker-pdf 1.x)
- 🔍 Advanced text extraction and OCR capabilities
- 📊 Table detection and formatting
- 🖼️ Image extraction and optimization
- 📝 Clean markdown output with preserved structure
- 📱 EPUB 3 generation with landmarks, hierarchical TOC, and page-list navigation
- ♿ Accessibility built in: language tags from `dc:language`, schema.org
  accessibility metadata, popup footnote semantics, alt-text drafting via Claude
- 🌍 Multi-language support
- 🚀 GPU acceleration support (NVIDIA, AMD, Apple Silicon)

## 🛠️ Dependencies

- Python 3.10–3.14 (3.13 recommended, see below)
- PyTorch (with CUDA/ROCm/MPS support for GPU acceleration)
- marker-pdf==1.10.2
- transformers==4.57.6
- markdown==3.10.2
- latex2mathml==3.81.0
- Pillow, pypdfium2, regex
- Optional: `anthropic` (alt-text drafting), `epubcheck` and `@daisy/ace`
  (validation)

### ⚠️ Python version

Python 3.13 is recommended.

`marker-pdf` constrains `Pillow<11.0.0`, and Pillow only ships Python 3.14
wheels from 11.3.0 onward. On Python 3.10–3.13 every dependency installs as a
prebuilt wheel. On 3.14, pip has to build Pillow from source instead: this
works, but it is slower and requires a working C toolchain plus the image
library headers Pillow links against. On Debian/Ubuntu install them first:

```bash
sudo apt install libjpeg-dev zlib1g-dev libtiff-dev libfreetype6-dev libwebp-dev
```

Without these headers the install fails with
`RequiredDependencyException: The headers or library files could not be found for jpeg`.

## 💻 Installation

1. Create and activate a virtual environment.

On Linux/Mac:
```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

On Windows:
```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
```

2. Install Python dependencies (this installs PyTorch as well):
```bash
pip install -r requirements.txt
```

3. GPU acceleration (optional):

PyTorch is installed as a dependency in step 2. On Apple Silicon that wheel
already supports MPS, so no further action is needed. For a specific CUDA or
ROCm build, reinstall PyTorch using the selector at
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/).
For example, for AMD GPUs with ROCm:
```bash
pip uninstall torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.2
```

4. Verify GPU support:
```python
import torch
print(torch.__version__)  # PyTorch version
print(torch.cuda.is_available())  # Should return True for NVIDIA
print(torch.backends.mps.is_available())  # Should return True for Apple Silicon
print(torch.version.hip)  # Should print ROCm version for AMD
```

### 🐳 Docker

A CPU-only image can be built from the included `Dockerfile`:

```bash
docker build -t pdf2epub .
```

Run it with your PDFs mounted at `/data` and a model cache volume (marker-pdf
downloads its models on first run):

```bash
docker run -it --rm \
  -v "$(pwd)":/data \
  -v pdf2epub-models:/models \
  pdf2epub input.pdf
```

`-it` is required for EPUB generation because metadata is prompted
interactively; with `--yes` or `--skip-epub` it can run non-interactively.

Tagged releases are published to `ghcr.io/<owner>/pdf2epub` by the Docker
workflow (the image name follows whichever repository it runs in).

## 🚀 Usage

### Basic Usage

```bash
python main.py input.pdf            # single file
python main.py input_directory/    # every PDF in a directory
```

### Options

EPUB generation prompts interactively for metadata (title, author, language,
and so on; press Enter to accept each default). It therefore needs a terminal —
without `--yes` a non-interactive run fails with `EOFError`. Use `--yes` to
build from `description.json` as-is, or `--skip-epub` for markdown only.

### Advanced Options

```bash
python main.py [input_path] [output_path] [options]

Options:
  --max-pages INT      Maximum number of pages to process
  --start-page INT     Page to start from (requires --max-pages)
  --skip-epub          Only create markdown
  --skip-md            Reuse existing markdown, only build the EPUB
  --langs STRING       Language(s), first becomes dc:language (e.g. "de")
  --use-llm            marker LLM mode for higher-quality structure
  --llm-service STR    e.g. "marker.services.claude.ClaudeService"
  --paginate           Keep page boundaries as {N}---- separators
  --no-images          Skip image extraction
  --yes, -y            Non-interactive EPUB build (description.json as-is)
```

If `input_path` is omitted, all PDFs in `./input/` are processed.

### Examples

```bash
python main.py book.pdf --langs de --paginate      # German book, keep page markers
python main.py thesis.pdf --skip-epub              # markdown only
python main.py book/ --skip-md --yes               # rebuild EPUB non-interactively
```

### Alt-text drafting (optional, needs ANTHROPIC_API_KEY)

```bash
python -m modules.alt_text book_dir/ --dry-run   # list images missing alt text
python -m modules.alt_text book_dir/             # draft alt text with Claude
```

Review the drafts before building the EPUB, then add `"alternativeText"` to
`accessibility.accessibilityFeature` in description.json.

### Output Structure

```
output_directory/
├── document_name/
│   ├── document_name.md
│   ├── document_name.epub
│   ├── document_name_metadata.json
│   ├── description.json        # seeded from PDF metadata — review dc:* fields
│   └── images/
```

### description.json

mark2epub builds the EPUB from `description.json`. Chapters are objects; the
optional `"title"` is the TOC label (use ASCII slug filenames), `"bodymatter":
true` marks the start of content for the landmarks nav, and an
`"accessibility"` object overrides the schema.org accessibility metadata
defaults. `"toc_depth": 1` disables the nested TOC level built from `##`
headings. Chapters may contain pagebreak spans
(`<span epub:type="pagebreak" role="doc-pagebreak" id="pageN" aria-label="N"></span>`)
which become an EPUB page-list nav automatically.

```json
{
  "metadata": {
    "dc:title": "…", "dc:creator": "…", "dc:identifier": "isbn-…",
    "dc:language": "de", "dc:source": "urn:isbn:…", "dc:date": "2020",
    "dc:rights": "…", "dc:publisher": "…"
  },
  "default_css": ["style.css"],
  "cover_image": "cover.jpeg",
  "accessibility": {"accessibilitySummary": "…"},
  "chapters": [
    {"markdown": "01-kapitel.md", "css": "style.css",
     "title": "1 Kapitelname", "bodymatter": true}
  ]
}
```

## 🧪 Tests

```bash
python3 -m unittest discover tests -v
```

## 🤝 Contributing

1. Fork the repository, create a branch, commit, push, open a PR.
2. Please include tests (`tests/test_pipeline.py` builds real EPUBs in temp
   dirs) and run epubcheck on a sample book.

## 📄 License

MIT — see [LICENSE](LICENSE).

## 🐛 Known Issues

- Some image embedding might need manual adjustment
- Complex mathematical equations might not be perfectly converted
- Multi-column layouts may require manual adjustment
- WebP/SVG images are not bundled (convert to JPEG/PNG first)
- See `docs/pipeline-improvements.md` for the improvement backlog

## 🙏 Acknowledgments

- [marker-pdf](https://github.com/VikParuchuri/marker) for PDF processing
- [mark2epub](https://github.com/AlexPof/mark2epub) for markdown conversion
- [PyTorch](https://pytorch.org/) for GPU acceleration
