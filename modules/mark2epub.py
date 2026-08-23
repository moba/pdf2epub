import markdown
import os
import shutil
from xml.dom import minidom
import zipfile
import sys
import json
from PIL import Image
import regex as re
from pathlib import Path
from datetime import datetime, timezone
import subprocess
from typing import Dict, Optional, Tuple
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape as xml_escape
import latex2mathml.converter

def get_user_input(prompt: str, default: str = "") -> str:
    """Get user input with a default value."""
    user_input = input(f"{prompt} [{default}]: ").strip()
    return user_input if user_input else default

def get_metadata_from_user(existing_metadata: Optional[Dict] = None, interactive: bool = True) -> Dict:
    """Collect metadata with defaults from existing metadata.
    interactive=False takes every default without prompting (--yes mode)."""
    if existing_metadata is None:
        existing_metadata = {}

    metadata = existing_metadata.get("metadata", {})

    fields = {
        "dc:title": ("Title", metadata.get("dc:title", "Untitled Document")),
        "dc:creator": ("Author(s)", metadata.get("dc:creator", "Unknown Author")),
        "dc:identifier": ("Unique Identifier", metadata.get("dc:identifier", f"id-{datetime.now().strftime('%Y%m%d%H%M%S')}")),
        "dc:language": ("Language (e.g., en, de, fr)", metadata.get("dc:language", "en")),
        "dc:rights": ("Rights", metadata.get("dc:rights", "All rights reserved")),
        "dc:publisher": ("Publisher", metadata.get("dc:publisher", "PDF2EPUB")),
        "dc:date": ("Publication Date (YYYY-MM-DD)", metadata.get("dc:date", datetime.now().strftime("%Y-%m-%d")))
    }

    updated_metadata = {}
    if interactive:
        print("\nPlease provide the following metadata for your EPUB (press Enter to use default value):")
    for key, (prompt, default) in fields.items():
        value = get_user_input(prompt, default) if interactive else default
        updated_metadata[key] = value

    # Preserve any extra metadata keys (dc:source, dc:description, …)
    for key, value in metadata.items():
        updated_metadata.setdefault(key, value)
        
    result = {
        "metadata": updated_metadata,
        "default_css": existing_metadata.get("default_css", ["style.css"]),
        "chapters": existing_metadata.get("chapters", []),
        "cover_image": existing_metadata.get("cover_image", None)
    }
    # Preserve optional keys across the rewrite. Anything dropped here is
    # silently lost for the whole build: main() works on this result, not on
    # the file, so e.g. image_max_dimension would fall back to its default
    # and every image would be embedded at 1800px/85% regardless of the
    # description.json.
    for key in ("accessibility", "toc_depth",
                "image_max_dimension", "image_quality"):
        if key in existing_metadata:
            result[key] = existing_metadata[key]
    return result

def review_markdown(markdown_path: Path) -> tuple[bool, str]:
    """Ask user if they want to review the markdown file."""
    content = markdown_path.read_text(encoding='utf-8')
    
    while True:
        response = input("\nWould you like to review the markdown file before conversion? (y/n): ").lower()
        if response in ['y', 'yes']:
            try:
                subprocess.run(['xdg-open' if os.name == 'posix' else 'start', str(markdown_path)], check=True)
                
                while True:
                    proceed = input("\nPress Enter when you're done editing (or 'q' to abort): ").lower()
                    if proceed == 'q':
                        return False, content
                    elif proceed == '':
                        updated_content = markdown_path.read_text(encoding='utf-8')
                        return True, updated_content
            except Exception as e:
                print(f"\nError opening markdown file: {e}")
                print("Proceeding with conversion...")
                return True, content
        elif response in ['n', 'no']:
            return True, content
        else:
            print("Please enter 'y' or 'n'")

def build_image_lookup(images_dir: Path) -> dict:
    """Build a {lowercase_name: actual_path} map for O(1) case-insensitive lookups."""
    lookup = {}
    try:
        for entry in images_dir.iterdir():
            lookup[entry.name.lower()] = entry
    except FileNotFoundError:
        pass
    return lookup

def process_markdown_for_images(markdown_text: str, work_dir: Path) -> tuple[str, list[str]]:
    """Process markdown content to find image references."""
    image_pattern = r'!\[(.*?)\]\((.*?)\)'
    images_found = []
    modified_text = markdown_text
    images_dir = work_dir / 'images'
    lookup = build_image_lookup(images_dir)

    for match in re.finditer(image_pattern, markdown_text):
        alt_text, image_path = match.groups()
        img_path = Path(image_path.strip())

        actual = lookup.get(img_path.name.lower())
        if actual is not None:
            images_found.append(actual.name)
            new_ref = f'![{alt_text}](images/{actual.name})'
            modified_text = modified_text.replace(match.group(0), new_ref)
            if not alt_text.strip():
                print(f"Note: image without alt text (screen readers will skip it): {actual.name}")
        else:
            print(f"Warning: Image not found: {images_dir / img_path.name}")

    return modified_text, images_found

def copy_and_optimize_image(src_path: Path, dest_path: Path, max_dimension: int = 1800, quality: int = 85) -> None:
    """Copy image to destination path with optimization for EPUB.
    JPEG/PNG are recompressed/downscaled; anything else (GIF, …) is passed
    through unchanged — a rename would break the manifest and markdown refs."""
    if src_path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
        shutil.copyfile(src_path, dest_path)
        return
    try:
        with Image.open(src_path) as img:
            if img.mode == 'RGBA':
                img = img.convert('RGB')

            ratio = min(max_dimension / max(img.size[0], img.size[1]), 1.0)
            new_size = tuple(int(dim * ratio) for dim in img.size)

            if ratio < 1.0:
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            if src_path.suffix.lower() == '.png':
                img.save(dest_path, 'PNG', optimize=True)
            else:
                img.save(dest_path, 'JPEG', quality=quality, optimize=True)

    except Exception as e:
        print(f"Error processing image {src_path}: {e}")
        raise

def get_all_filenames(the_dir, extensions=[]):
    if not os.path.exists(the_dir):
        return []
    all_files = [x for x in os.listdir(the_dir)]
    all_files = [x for x in all_files if x.split(".")[-1] in extensions]
    return all_files

def get_publication_language(description_data) -> str:
    """BCP 47 language of the publication from dc:language (default 'en')."""
    try:
        lang = description_data["metadata"].get("dc:language", "").strip()
    except (KeyError, TypeError, AttributeError):
        lang = ""
    return lang or "en"

def extract_sections(xhtml: str) -> list[tuple[str, str]]:
    """(id, text) of every <h2> in a chapter's XHTML — the nested TOC level."""
    sections = []
    for m in re.finditer(r'<h2 id="([^"]+)">(.*?)</h2>', xhtml, re.DOTALL):
        label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if label:
            sections.append((m.group(1), label))
    return sections

def extract_pagebreaks(xhtml: str, href: str) -> list[tuple[str, str, str]]:
    """Find pagebreak spans (epub:type="pagebreak") in a chapter's XHTML.
    Returns (href, id, label) per break, in document order."""
    breaks = []
    for m in re.finditer(r'<span[^>]*?epub:type="pagebreak"[^>]*?>', xhtml):
        tag = m.group(0)
        m_id = re.search(r'id="([^"]+)"', tag)
        if not m_id:
            continue
        m_label = re.search(r'aria-label="([^"]+)"', tag)
        breaks.append((href, m_id.group(1),
                       m_label.group(1) if m_label else m_id.group(1)))
    return breaks

def get_packageOPF_XML(md_filenames=[], image_filenames=[], css_filenames=[], description_data=None,
                       mathml_filenames=(), has_pagebreaks=False):
    doc = minidom.Document()
    pub_lang = get_publication_language(description_data)

    package = doc.createElement('package')
    package.setAttribute('xmlns',"http://www.idpf.org/2007/opf")
    package.setAttribute('version',"3.0")
    package.setAttribute('xml:lang', pub_lang)
    package.setAttribute("unique-identifier","pub-id")

    ## Now building the metadata

    metadata = doc.createElement('metadata')
    metadata.setAttribute('xmlns:dc', 'http://purl.org/dc/elements/1.1/')

    date_value = description_data["metadata"].get("dc:date", "")
    if date_value and not re.fullmatch(
            r"\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?",
            date_value.strip()):
        print(f"Warning: dc:date '{date_value}' is not W3CDTF "
              "(use YYYY, YYYY-MM or YYYY-MM-DD)")

    for k,v in description_data["metadata"].items():
        if len(v):
            x = doc.createElement(k)
            for metadata_type,id_label in [("dc:title","title"),("dc:creator","creator"),("dc:identifier","pub-id")]:
                if k==metadata_type:
                    x.setAttribute('id',id_label)
            x.appendChild(doc.createTextNode(v))
            metadata.appendChild(x)

    # Required by EPUB 3: dcterms:modified timestamp
    modified_meta = doc.createElement('meta')
    modified_meta.setAttribute('property', 'dcterms:modified')
    modified_meta.appendChild(doc.createTextNode(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    metadata.appendChild(modified_meta)

    # Schema.org accessibility metadata (EPUB Accessibility 1.1).
    # Defaults describe what this generator actually produces; override or
    # extend via an "accessibility" object in description.json.
    a11y_defaults = {
        "schema:accessMode": ["textual", "visual"] if image_filenames else ["textual"],
        "schema:accessModeSufficient": ["textual"],
        "schema:accessibilityFeature": ["tableOfContents", "readingOrder", "structuralNavigation"],
        "schema:accessibilityHazard": ["none"],
    }
    a11y_config = (description_data or {}).get("accessibility", {})
    for prop, defaults in a11y_defaults.items():
        short = prop.split(":")[1]
        values = a11y_config.get(short, defaults)
        if isinstance(values, str):
            values = [values]
        if (short == "accessibilityFeature" and has_pagebreaks
                and "pageBreakMarkers" not in values):
            values = list(values) + ["pageBreakMarkers"]
        for value in values:
            m = doc.createElement('meta')
            m.setAttribute('property', prop)
            m.appendChild(doc.createTextNode(value))
            metadata.appendChild(m)
    summary = a11y_config.get("accessibilitySummary")
    if summary:
        m = doc.createElement('meta')
        m.setAttribute('property', 'schema:accessibilitySummary')
        m.appendChild(doc.createTextNode(summary))
        metadata.appendChild(m)

    # Page-list support: identify the print source of the page numbers
    if has_pagebreaks:
        source = description_data["metadata"].get("dc:source", "")
        if source:
            m = doc.createElement('meta')
            m.setAttribute('property', 'pageBreakSource')
            m.appendChild(doc.createTextNode(source))
            metadata.appendChild(m)


    ## Now building the manifest

    manifest = doc.createElement('manifest')

    ## TOC.xhtml file for EPUB 3
    x = doc.createElement('item')
    x.setAttribute('id',"toc")
    x.setAttribute('properties',"nav")
    x.setAttribute('href',"TOC.xhtml")
    x.setAttribute('media-type',"application/xhtml+xml")
    manifest.appendChild(x)

    ## Ensure retrocompatibility by also providing a TOC.ncx file
    x = doc.createElement('item')
    x.setAttribute('id',"ncx")
    x.setAttribute('href',"toc.ncx")
    x.setAttribute('media-type',"application/x-dtbncx+xml")
    manifest.appendChild(x)

    x = doc.createElement('item')
    x.setAttribute('id',"titlepage")
    x.setAttribute('href',"titlepage.xhtml")
    x.setAttribute('media-type',"application/xhtml+xml")
    manifest.appendChild(x)

    for i,md_filename in enumerate(md_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"s{:05d}".format(i))
        x.setAttribute('href', quote("s{:05d}-{}.xhtml".format(i, md_filename.split(".")[0])))
        x.setAttribute('media-type',"application/xhtml+xml")
        if md_filename in mathml_filenames:
            x.setAttribute('properties',"mathml")
        manifest.appendChild(x)

    for i,image_filename in enumerate(image_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"image-{:05d}".format(i))
        x.setAttribute('href', "images/{}".format(quote(image_filename)))
        ext = Path(image_filename).suffix.lower()
        if ext == ".gif":
            x.setAttribute('media-type',"image/gif")
        elif ext in (".jpg", ".jpeg"):
            x.setAttribute('media-type',"image/jpeg")
        elif ext == ".png":
            x.setAttribute('media-type',"image/png")
        if image_filename==description_data["cover_image"]:
            x.setAttribute('properties',"cover-image")

            ## Ensure compatibility by also providing a meta tag in the metadata
            y = doc.createElement('meta')
            y.setAttribute('name',"cover")
            y.setAttribute('content',"image-{:05d}".format(i))
            metadata.appendChild(y)
        manifest.appendChild(x)

    for i,css_filename in enumerate(css_filenames):
        x = doc.createElement('item')
        x.setAttribute('id',"css-{:05d}".format(i))
        x.setAttribute('href',"css/{}".format(css_filename))
        x.setAttribute('media-type',"text/css")
        manifest.appendChild(x)

    ## Now building the spine

    spine = doc.createElement('spine')
    spine.setAttribute('toc', "ncx")

    x = doc.createElement('itemref')
    x.setAttribute('idref',"titlepage")
    x.setAttribute('linear',"yes")
    spine.appendChild(x)
    # Nav document in the spine so the landmarks nav may reference it and
    # readers get an in-book contents page
    x = doc.createElement('itemref')
    x.setAttribute('idref',"toc")
    x.setAttribute('linear',"yes")
    spine.appendChild(x)
    for i,_ in enumerate(md_filenames):
        x = doc.createElement('itemref')
        x.setAttribute('idref',"s{:05d}".format(i))
        x.setAttribute('linear',"yes")
        spine.appendChild(x)

    guide = doc.createElement('guide')
    x = doc.createElement('reference')
    x.setAttribute('type',"cover")
    x.setAttribute('title',"Cover image")
    x.setAttribute('href',"titlepage.xhtml")
    guide.appendChild(x)


    package.appendChild(metadata)
    package.appendChild(manifest)
    package.appendChild(spine)
    package.appendChild(guide)
    doc.appendChild(package)

    return doc.toprettyxml()


def get_container_XML():
    container_data = """<?xml version="1.0" encoding="UTF-8" ?>\n"""
    container_data += """<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n"""
    container_data += """<rootfiles>\n"""
    container_data += """<rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>\n"""
    container_data += """</rootfiles>\n</container>"""
    return container_data

def get_coverpage_XML(title, authors, lang="en", cover_image=None):
    """Generate the cover/title page. Shows the cover image when one is set,
    otherwise falls back to a typographic title page."""
    if cover_image:
        content = """<img class="cover-image" src="images/{}" alt="{}"/>""".format(
            quote(cover_image), xml_escape(f"Cover: {title}" + (f" – {authors}" if authors else "")))
    else:
        content = "<h1>{}</h1>\n        <p>{}</p>".format(xml_escape(title), xml_escape(authors or ""))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head>
<title>{xml_escape(title)}</title>
<style type="text/css">
body {{
    margin: 0;
    padding: 0;
    text-align: center;
    font-family: serif;
}}
.cover {{
    margin: 1em auto;
    padding: 1em;
    max-width: 95%;
}}
.cover-image {{
    max-width: 100%;
    max-height: 95vh;
    height: auto;
}}
h1 {{
    font-size: 2em;
    margin: 2em 0 1em 0;
    line-height: 1.2;
}}
p {{
    font-size: 1.2em;
    font-style: italic;
    line-height: 1.4;
}}
</style>
</head>
<body epub:type="cover">
    <section class="cover">
        {content}
    </section>
</body>
</html>"""

def _gruppenbaum(md_filenames, groups):
    """[(pfad, md_filename, index)] in Dokumentreihenfolge.

    pfad ist die group-Liste des Kapitels (leer, wenn keine gesetzt ist).
    """
    return [(list(groups.get(md, [])), md, i) for i, md in enumerate(md_filenames)]


def _gemeinsamer_praefix(a, b):
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def get_TOC_XML(default_css_filenames, markdown_filenames, chapter_titles=None, lang="en", start_index=0,
                page_list=None, sections=None, groups=None):
    ## Returns the XML data for the TOC.xhtml file (EPUB 3 nav document).
    ## sections: {md_filename: [(fragment_id, label), ...]} for a nested level.
    ## groups: {md_filename: [level1, level2, ...]} for nested TOC groups
    ##         (e.g. year -> issue -> section) rendered as <span> nodes.
    if chapter_titles is None:
        chapter_titles = [f.split(".")[0] for f in markdown_filenames]
    sections = sections or {}

    toc_xhtml = """<?xml version="1.0" encoding="UTF-8"?>\n"""
    toc_xhtml += """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{0}" xml:lang="{0}">\n""".format(lang)
    toc_xhtml += """<head>\n<meta http-equiv="default-style" content="text/html; charset=utf-8"/>\n"""
    toc_xhtml += """<title>Contents</title>\n"""

    for css_filename in default_css_filenames:
        toc_xhtml += """<link rel="stylesheet" href="css/{}" type="text/css"/>\n""".format(css_filename)

    toc_xhtml += """</head>\n<body>\n"""
    groups = groups or {}
    toc_xhtml += """<nav epub:type="toc" role="doc-toc" id="toc">\n<h2>Contents</h2>\n<ol>"""
    hrefs = []
    for i, md_filename in enumerate(markdown_filenames):
        stem = md_filename.split(".")[0]
        hrefs.append(quote("s{:05d}-{}.xhtml".format(i, stem)))

    offen = []
    for pfad, md_filename, i in _gruppenbaum(markdown_filenames, groups):
        gemeinsam = _gemeinsamer_praefix(offen, pfad)
        for _ in range(len(offen) - gemeinsam):
            toc_xhtml += "</ol></li>"
        for stufe in pfad[gemeinsam:]:
            toc_xhtml += "<li><span>{}</span><ol>".format(xml_escape(stufe))
        offen = list(pfad)

        href = hrefs[i]
        toc_xhtml += """<li><a href="{}">{}</a>""".format(
            href, xml_escape(chapter_titles[i]))
        chapter_sections = (sections or {}).get(md_filename, [])
        if chapter_sections:
            toc_xhtml += "<ol>"
            for frag_id, label in chapter_sections:
                toc_xhtml += """<li><a href="{}#{}">{}</a></li>""".format(
                    href, frag_id, label)
            toc_xhtml += "</ol>"
        toc_xhtml += "</li>"
    for _ in range(len(offen)):
        toc_xhtml += "</ol></li>"
    toc_xhtml += """</ol>\n</nav>\n"""
    if hrefs:
        start = min(start_index, len(hrefs) - 1)
        toc_xhtml += """<nav epub:type="landmarks" hidden="hidden">\n<h2>Landmarks</h2>\n<ol>"""
        toc_xhtml += """<li><a epub:type="cover" href="titlepage.xhtml">Cover</a></li>"""
        toc_xhtml += """<li><a epub:type="toc" href="TOC.xhtml">Table of Contents</a></li>"""
        toc_xhtml += """<li><a epub:type="bodymatter" href="{}">Start of Content</a></li>""".format(hrefs[start])
        toc_xhtml += """</ol>\n</nav>\n"""
    if page_list:
        toc_xhtml += """<nav epub:type="page-list" hidden="hidden">\n<h2>List of Pages</h2>\n<ol>"""
        for href, frag_id, label in page_list:
            toc_xhtml += """<li><a href="{}#{}">{}</a></li>""".format(
                href, frag_id, xml_escape(label))
        toc_xhtml += """</ol>\n</nav>\n"""
    toc_xhtml += """</body>\n</html>"""

    return toc_xhtml

def get_TOCNCX_XML(markdown_filenames, uid="", title="", chapter_titles=None,
                   lang="en", groups=None):
    ## Returns the XML data for the TOC.ncx file
    ## groups: {md_filename: [level1, level2, ...]} for nested navPoints,
    ##         mirroring get_TOC_XML's groups. A group navPoint's content
    ##         src points at its first child's href (the NCX has no <span>).
    if chapter_titles is None:
        chapter_titles = [f.split(".")[0] for f in markdown_filenames]
    groups = groups or {}
    hrefs = [quote("s{:05d}-{}.xhtml".format(i, md.split(".")[0]))
             for i, md in enumerate(markdown_filenames)]

    tiefe = max([len(groups.get(md, [])) + 1 for md in markdown_filenames] or [1])
    ordnung = {"n": 0}    # aufsteigender Zähler nur für Blätter (playOrder)
    id_zaehler = {"n": 0}  # separater, immer hochzählender Zähler für eindeutige ids

    def navpoint(label, src, order, inhalt=""):
        id_zaehler["n"] += 1
        return ('<navPoint id="navpoint-{0}" playOrder="{1}">\n'
                "<navLabel>\n<text>{2}</text>\n</navLabel>"
                '<content src="{3}"/>{4}</navPoint>').format(
                    id_zaehler["n"], order, xml_escape(label), src, inhalt)

    def baue(eintraege, ebene):
        """Liefert (xml, erste_playorder) für diesen Teilbaum.

        Nur Kapitel bekommen eine neue Nummer; eine Gruppe übernimmt die ihres
        ersten Blattes. Andernfalls trügen mehrere navPoints für dasselbe Ziel
        verschiedene playOrder-Werte — epubcheck RSC-005.
        """
        teile, erste = [], None
        i = 0
        while i < len(eintraege):
            pfad, md, idx = eintraege[i]
            if len(pfad) <= ebene:
                ordnung["n"] += 1
                n = ordnung["n"]
                teile.append(navpoint(chapter_titles[idx], hrefs[idx], n))
                erste = erste if erste is not None else n
                i += 1
                continue
            j = i
            while (j < len(eintraege) and len(eintraege[j][0]) > ebene
                   and eintraege[j][0][ebene] == pfad[ebene]):
                j += 1
            kind_xml, kind_erste = baue(eintraege[i:j], ebene + 1)
            teile.append(navpoint(pfad[ebene], hrefs[eintraege[i][2]],
                                  kind_erste, "\n" + kind_xml))
            erste = erste if erste is not None else kind_erste
            i = j
        return "\n".join(teile), (erste if erste is not None else 1)

    navmap, _ = baue(_gruppenbaum(markdown_filenames, groups), 0)

    toc_ncx = """<?xml version="1.0" encoding="UTF-8"?>\n"""
    toc_ncx += """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" xml:lang="{}" version="2005-1">\n""".format(lang)
    toc_ncx += "<head>\n"
    toc_ncx += """<meta name="dtb:uid" content="{}"/>\n""".format(xml_escape(uid))
    toc_ncx += """<meta name="dtb:depth" content="{}"/>\n""".format(tiefe)
    toc_ncx += """<meta name="dtb:totalPageCount" content="0"/>\n"""
    toc_ncx += """<meta name="dtb:maxPageNumber" content="0"/>\n"""
    toc_ncx += "</head>\n"
    toc_ncx += """<docTitle><text>{}</text></docTitle>\n""".format(xml_escape(title))
    toc_ncx += "<navMap>\n" + navmap + "\n</navMap>\n</ncx>"
    return toc_ncx

def convert_math_to_mathml(html_text: str) -> str:
    """Replace LaTeX math expressions in HTML with MathML, skipping code blocks."""
    # Mask <pre>/<code> blocks so their $ delimiters are never treated as math
    placeholders = {}
    counter = [0]

    def mask(m):
        key = f"\x00MASK{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = m.group(0)
        return key

    masked = re.sub(r'<pre[\s\S]*?</pre>|<code[\s\S]*?</code>', mask, html_text, flags=re.DOTALL)

    def try_convert(latex):
        try:
            return latex2mathml.converter.convert(latex)
        except Exception:
            return None

    # Standalone display math that Markdown wrapped in <p>: replace the whole
    # paragraph to avoid invalid XHTML like <p><div>...</div></p>
    def replace_display_paragraph(m):
        mathml = try_convert(m.group(1))
        return f'<div class="math-display">{mathml}</div>' if mathml else m.group(0)

    # Remaining $$...$$ (inside phrasing content): use <span> to stay valid
    def replace_display_inline(m):
        mathml = try_convert(m.group(1))
        return f'<span class="math-display">{mathml}</span>' if mathml else m.group(0)

    # Inline $...$
    def replace_inline(m):
        mathml = try_convert(m.group(1))
        return f'<span class="math-inline">{mathml}</span>' if mathml else m.group(0)

    masked = re.sub(r'<p>\s*\$\$(.*?)\$\$\s*</p>', replace_display_paragraph, masked, flags=re.DOTALL)
    masked = re.sub(r'\$\$(.*?)\$\$', replace_display_inline, masked, flags=re.DOTALL)
    masked = re.sub(r'(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)', replace_inline, masked, flags=re.DOTALL)

    for key, original in placeholders.items():
        masked = masked.replace(key, original)

    return masked

def add_footnote_semantics(html_text: str) -> str:
    """Add epub:type / DPUB-ARIA semantics to python-markdown footnote output
    so reading systems can offer popup footnotes and screen readers announce
    note references properly."""
    html_text = html_text.replace(
        '<a class="footnote-ref"',
        '<a epub:type="noteref" role="doc-noteref" class="footnote-ref"')
    html_text = html_text.replace(
        '<div class="footnote">',
        '<div class="footnote" epub:type="endnotes" role="doc-endnotes">')
    html_text = re.sub(
        r'<li id="fn:',
        '<li epub:type="footnote" id="fn:',
        html_text)
    html_text = html_text.replace(
        '<a class="footnote-backref"',
        '<a epub:type="backlink" role="doc-backlink" class="footnote-backref"')
    return html_text

def rewrite_internal_links(xhtml: str, href_by_md: dict) -> str:
    """Rewrite intra-book links to *.md files to the generated XHTML hrefs.

    Markdown chapters link to each other by their source filename (an index
    or register linking to its articles). Those names do not exist in the
    EPUB, where every chapter becomes sNNNNN-<stem>.xhtml — left alone, each
    such link is a dangling reference (epubcheck RSC-007). Fragments are
    kept, absolute URLs are left untouched.
    """
    def ersetze(m):
        ziel = unquote(m.group(2))
        href = href_by_md.get(ziel) or href_by_md.get(ziel.split("/")[-1])
        if href is None:
            return m.group(0)
        return '{}"{}{}"'.format(m.group(1), href, m.group(3) or "")

    return re.sub(r'(href=)"(?!\w+:)([^"#]+\.md)(#[^"]*)?"', ersetze, xhtml)


def get_chapter_XML(work_dir: str, md_filename: str, css_filenames: list[str], content: Optional[str] = None,
                    title: Optional[str] = None, lang: str = "en") -> tuple[str, list[str]]:
    """
    Convert markdown chapter to XHTML and process images.
    Returns tuple of (XHTML content, list of images referenced in chapter)

    Args:
        work_dir: Working directory containing markdown files
        md_filename: Name of markdown file
        css_filenames: List of CSS files to include
        content: Optional pre-loaded markdown content. If None, content is read from file
        title: Chapter title for the <title> element (default: filename stem)
        lang: BCP 47 language tag for the chapter content
    """
    work_dir_path = Path(work_dir)
    
    if content is None:
        with open(work_dir_path / md_filename, "r", encoding="utf-8") as f:
            markdown_data = f.read()
    else:
        markdown_data = content
    
    # Process markdown for images and get list of referenced images
    markdown_data, chapter_images = process_markdown_for_images(markdown_data, work_dir_path)
    
    # Convert to HTML ("toc" adds slug ids to headings for deep links / nested nav)
    html_text = markdown.markdown(
        markdown_data,
        extensions=["codehilite", "tables", "fenced_code", "footnotes", "toc"],
        extension_configs={"codehilite": {"guess_lang": False}}
    )

    # Convert LaTeX math to MathML for EPUB readers
    html_text = convert_math_to_mathml(html_text)

    # Popup-footnote / screen-reader semantics
    html_text = add_footnote_semantics(html_text)

    # Generate XHTML wrapper
    chapter_title = title or os.path.splitext(md_filename)[0]
    xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:m="http://www.w3.org/1998/Math/MathML" lang="{lang}" xml:lang="{lang}">
<head>
    <meta http-equiv="default-style" content="text/html; charset=utf-8"/>
    <title>{xml_escape(chapter_title)}</title>
    {''.join(f'<link rel="stylesheet" href="css/{css}" type="text/css" media="all"/>' for css in css_filenames)}
</head>
<body>
{html_text}
</body>
</html>"""

    return xhtml, chapter_images



def convert_to_epub(markdown_dir: Path, output_path: Path, non_interactive: bool = False) -> None:
    """
    Convert markdown files and images to EPUB format.
    """
    if not markdown_dir.exists():
        raise FileNotFoundError(f"Markdown directory not found: {markdown_dir}")

    if not list(markdown_dir.glob('*.md')):
        raise ValueError(f"No markdown files found in: {markdown_dir}")

    # Generate EPUB file
    epub_path = markdown_dir / f"{markdown_dir.name}.epub"
    args = ["--yes"] if non_interactive else []
    main(args + [str(markdown_dir), str(epub_path)])

def main(args):
    args = list(args)
    interactive = True
    for flag in ("--yes", "-y"):
        if flag in args:
            args.remove(flag)
            interactive = False
    if len(args) < 2:
        print("\nUsage:\n    python mark2epub.py [--yes] <markdown_directory> <output_file.epub>\n"
              "    --yes / -y : non-interactive — accept description.json as-is, no review prompts")
        exit(1)

    work_dir = args[0]
    output_path = args[1]

    images_dir = os.path.join(work_dir, 'images/')
    css_dir = os.path.join(work_dir, 'css/')

    try:
        # Reading/Creating the JSON file containing the description of the eBook
        description_path = os.path.join(work_dir, "description.json")
        existing_metadata = {}
        
        if os.path.exists(description_path):
            with open(description_path, 'r', encoding='utf-8') as f:
                existing_metadata = json.load(f)
        
        # Get metadata from user (or take description.json as-is with --yes)
        json_data = get_metadata_from_user(existing_metadata, interactive=interactive)
        
        # Find all markdown files if not already in metadata
        if not json_data["chapters"]:
            markdown_files = [f for f in os.listdir(work_dir) if f.endswith('.md')]
            for md_file in sorted(markdown_files):
                json_data["chapters"].append({
                    "markdown": md_file,
                    "css": ""
                })
        
        # Save the updated description.json
        with open(description_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2)
        
        # Review markdown files and store updated content
        chapter_contents = {}
        for chapter in json_data["chapters"]:
            md_path = Path(work_dir) / chapter["markdown"]
            if interactive:
                should_continue, content = review_markdown(md_path)
                if not should_continue:
                    print("\nConversion aborted by user.")
                    return
            else:
                content = md_path.read_text(encoding='utf-8')
            chapter_contents[chapter["markdown"]] = content

        # Get title, author and publication language
        title = json_data["metadata"].get("dc:title", "Untitled Document")
        authors = json_data["metadata"].get("dc:creator", None)
        pub_lang = get_publication_language(json_data)

        # Compile list of files. A chapter may carry an optional "title" used
        # for the TOC label and chapter <title> (default: filename stem).
        all_md_filenames = []
        all_md_titles = []
        all_md_groups = {}
        all_css_filenames = json_data["default_css"][:]
        bodymatter_start = 0
        for chapter in json_data["chapters"]:
            if chapter["markdown"] not in all_md_filenames:
                all_md_filenames.append(chapter["markdown"])
                all_md_titles.append(chapter.get("title") or chapter["markdown"].split(".")[0])
                if chapter.get("bodymatter") and bodymatter_start == 0:
                    bodymatter_start = len(all_md_filenames) - 1
            if chapter.get("group"):
                all_md_groups[chapter["markdown"]] = list(chapter["group"])
            if len(chapter["css"]) and (chapter["css"] not in all_css_filenames):
                all_css_filenames.append(chapter["css"])
        chapter_title_map = dict(zip(all_md_filenames, all_md_titles))
        
        all_image_filenames = get_all_filenames(images_dir, extensions=["gif", "jpg", "jpeg", "png"])

        # First process all chapters and images
        images_dir = Path(work_dir) / 'images'
        epub_images_dir = Path(work_dir) / 'epub_images'
        processed_images = {}  # Store processed image data
        all_referenced_images = set()
        chapter_data = {}  # Store processed chapter data

        # First pass: Process chapters and collect image references
        print("\nProcessing chapters and collecting image references...")
        for i, chapter in enumerate(json_data["chapters"]):
            css_files = json_data["default_css"][:]
            if chapter["css"] and chapter["css"] not in css_files:
                css_files.append(chapter["css"])

            # Process chapter content
            chapter_xhtml, chapter_images = get_chapter_XML(
                work_dir,
                chapter["markdown"],
                css_files,
                content=chapter_contents[chapter["markdown"]],
                title=chapter_title_map.get(chapter["markdown"]),
                lang=chapter.get("language") or pub_lang
            )
            chapter_data[chapter["markdown"]] = chapter_xhtml
            all_referenced_images.update(chapter_images)

        # Chapter-to-chapter links: *.md in the markdown, sNNNNN-*.xhtml here
        href_by_md = {
            md: quote("s{:05d}-{}.xhtml".format(i, md.split(".")[0]))
            for i, md in enumerate(all_md_filenames)}
        for md_filename, xhtml in chapter_data.items():
            chapter_data[md_filename] = rewrite_internal_links(xhtml, href_by_md)

        # Process and optimize images
        print("\nProcessing and optimizing images...")
        if images_dir.exists() and all_referenced_images:
            epub_images_dir.mkdir(exist_ok=True)
            
            for image in all_referenced_images:
                src_path = images_dir / image
                if src_path.exists():
                    try:
                        dest_path = epub_images_dir / image
                        copy_and_optimize_image(
                            src_path, dest_path,
                            max_dimension=json_data.get("image_max_dimension", 1800),
                            quality=json_data.get("image_quality", 85))
                        
                        # Store processed image data
                        with open(dest_path, "rb") as f:
                            processed_images[image] = f.read()
                    except Exception as e:
                        print(f"Warning: Failed to process image {image}: {e}")
                else:
                    print(f"Warning: Referenced image not found: {src_path}")
            
            # Cleanup temporary directory
            shutil.rmtree(epub_images_dir, ignore_errors=True)

        # Now create the EPUB file with all prepared content
        print("\nCreating EPUB file...")
        with zipfile.ZipFile(output_path, "w") as epub:
            # Write mimetype (must be first and uncompressed)
            epub.writestr("mimetype", "application/epub+zip")

            # Write container.xml
            epub.writestr("META-INF/container.xml", get_container_XML(), zipfile.ZIP_DEFLATED)

            # Write package.opf
            mathml_files = {name for name, xhtml in chapter_data.items() if "<math" in xhtml}
            page_list = []
            for i, md_filename in enumerate(all_md_filenames):
                href = quote("s{:05d}-{}.xhtml".format(i, md_filename.split(".")[0]))
                page_list.extend(extract_pagebreaks(chapter_data[md_filename], href))
            # Nested TOC level from ## headings ("toc_depth": 1 disables)
            toc_sections = {}
            if json_data.get("toc_depth", 2) >= 2:
                toc_sections = {md: extract_sections(chapter_data[md])
                                for md in all_md_filenames}
            epub.writestr("OPS/package.opf",
                get_packageOPF_XML(
                    md_filenames=all_md_filenames,
                    image_filenames=all_image_filenames,
                    css_filenames=all_css_filenames,
                    description_data=json_data,
                    mathml_filenames=mathml_files,
                    has_pagebreaks=bool(page_list)
                ),
                zipfile.ZIP_DEFLATED
            )

            # Write cover page
            coverpage_data = get_coverpage_XML(title, authors, lang=pub_lang,
                                               cover_image=json_data.get("cover_image"))
            epub.writestr("OPS/titlepage.xhtml", coverpage_data.encode('utf-8'), zipfile.ZIP_DEFLATED)

            # Write processed chapters
            print("Writing chapters...")
            for i, chapter in enumerate(json_data["chapters"]):
                print(f"  Writing chapter {i+1}/{len(json_data['chapters'])}: {chapter['markdown']}")
                epub.writestr(
                    f"OPS/s{i:05d}-{chapter['markdown'].split('.')[0]}.xhtml",
                    chapter_data[chapter["markdown"]].encode('utf-8'),
                    zipfile.ZIP_DEFLATED
                )

            # Write processed images
            if processed_images:
                print(f"Writing {len(processed_images)} processed images...")
                for image_name, image_data in processed_images.items():
                    epub.writestr(f"OPS/images/{image_name}", image_data, zipfile.ZIP_DEFLATED)

            # Write TOC files
            print("Writing table of contents...")
            epub.writestr("OPS/TOC.xhtml",
                get_TOC_XML(json_data["default_css"], all_md_filenames,
                            chapter_titles=all_md_titles, lang=pub_lang,
                            start_index=bodymatter_start,
                            page_list=page_list, sections=toc_sections,
                            groups=all_md_groups),
                zipfile.ZIP_DEFLATED
            )

            epub.writestr("OPS/toc.ncx",
                get_TOCNCX_XML(
                    all_md_filenames,
                    uid=json_data["metadata"].get("dc:identifier", ""),
                    title=json_data["metadata"].get("dc:title", ""),
                    chapter_titles=all_md_titles,
                    lang=pub_lang,
                    groups=all_md_groups
                ),
                zipfile.ZIP_DEFLATED
            )

            # Copy remaining images that weren't referenced in markdown
            remaining_images = set(all_image_filenames) - set(processed_images.keys())
            if remaining_images and os.path.exists(images_dir):
                print(f"Writing {len(remaining_images)} additional images...")
                for image in remaining_images:
                    with open(os.path.join(images_dir, image), "rb") as f:
                        epub.writestr(f"OPS/images/{image}", f.read(), zipfile.ZIP_DEFLATED)

            # Copy CSS files; write a default style.css for any that are missing
            default_css_content = b"""body { font-family: serif; line-height: 1.5; margin: 5%; }
h1, h2, h3, h4, h5, h6 { font-family: sans-serif; }
img { max-width: 100%; height: auto; }
pre, code { font-family: monospace; font-size: 0.9em; }
"""
            print(f"Writing {len(all_css_filenames)} CSS files...")
            for css in all_css_filenames:
                css_path = os.path.join(css_dir, css)
                if os.path.exists(css_path):
                    with open(css_path, "rb") as f:
                        epub.writestr(f"OPS/css/{css}", f.read(), zipfile.ZIP_DEFLATED)
                else:
                    epub.writestr(f"OPS/css/{css}", default_css_content, zipfile.ZIP_DEFLATED)

        print(f"\nEPUB creation complete: {output_path}")
        
    except Exception as e:
        import traceback
        print(f"Error processing {work_dir}:")
        print(traceback.format_exc())
        raise

if __name__ == "__main__":
    main(sys.argv[1:])
