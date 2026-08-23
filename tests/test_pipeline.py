#!/usr/bin/env python3
"""Tests for the markdown→EPUB half of the pipeline (mark2epub) and the
description.json seeding in pdf2md. Run:  python3 -m unittest discover tests -v

They build real EPUBs into temp dirs and assert on the zip contents; no mocks.
"""
import builtins
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import mark2epub  # noqa: E402

from PIL import Image  # noqa: E402


def make_book(root: Path, chapters=None, metadata=None, extra=None):
    """Write a minimal book work dir (description.json + chapters + images)."""
    (root / "images").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), "white").save(root / "images" / "cover.jpeg", "JPEG")
    desc = {
        "metadata": {
            "dc:title": "Testbuch", "dc:creator": "Autorin",
            "dc:identifier": "id-test-1", "dc:language": "de",
            "dc:rights": "r", "dc:publisher": "p", "dc:date": "2020",
        },
        "default_css": ["style.css"],
        "cover_image": "cover.jpeg",
        "chapters": [],
    }
    if metadata:
        desc["metadata"].update(metadata)
    if extra:
        desc.update(extra)
    chapters = chapters or {"kapitel-eins.md": "# Eins\n\nText.\n"}
    for fname, content in chapters.items():
        (root / fname).write_text(content, encoding="utf-8")
        desc["chapters"].append(
            {"markdown": fname, "css": "", "title": fname.rsplit(".", 1)[0]})
    (root / "description.json").write_text(
        json.dumps(desc, ensure_ascii=False), encoding="utf-8")
    return desc


def no_input(*a, **k):
    raise AssertionError("input() called — build was not non-interactive")


class BuildCase(unittest.TestCase):
    """Base: builds a book with --yes into a temp dir, input() forbidden."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.out = self.dir / "out.epub"
        self._input = builtins.input
        builtins.input = no_input
        self.addCleanup(setattr, builtins, "input", self._input)

    def build(self, **kw):
        make_book(self.dir, **kw)
        mark2epub.main(["--yes", str(self.dir), str(self.out)])
        return zipfile.ZipFile(self.out)


class NonInteractiveBuild(BuildCase):
    """--yes must build straight from description.json without any prompt."""

    def test_yes_flag_builds_without_prompts(self):
        z = self.build()
        names = z.namelist()
        self.assertIn("OPS/package.opf", names)
        self.assertIn("OPS/s00000-kapitel-eins.xhtml", names)
        self.assertIn("mimetype", names)

    def test_extra_metadata_keys_survive_into_opf(self):
        z = self.build(metadata={"dc:source": "urn:isbn:9783000000000"})
        opf = z.read("OPS/package.opf").decode()
        self.assertIn("urn:isbn:9783000000000", opf)


PAGEBREAK = ('<span epub:type="pagebreak" role="doc-pagebreak" '
             'id="page12" aria-label="12"></span>')


class PageListNav(BuildCase):
    """Pagebreak spans in chapters must yield a page-list nav + metadata."""

    def test_pagebreak_spans_produce_page_list_nav(self):
        z = self.build(
            chapters={"kapitel-eins.md":
                      f"# Eins\n\nSeite elf.\n\n{PAGEBREAK}\n\nSeite zwölf.\n"},
            metadata={"dc:source": "urn:isbn:9783000000000"})
        toc = z.read("OPS/TOC.xhtml").decode()
        self.assertIn('epub:type="page-list"', toc)
        self.assertIn('href="s00000-kapitel-eins.xhtml#page12">12</a>', toc)
        opf = z.read("OPS/package.opf").decode()
        self.assertIn('property="pageBreakSource"', opf)
        self.assertIn(">pageBreakMarkers<", opf)

    def test_no_pagebreaks_no_page_list(self):
        z = self.build()
        toc = z.read("OPS/TOC.xhtml").decode()
        self.assertNotIn('epub:type="page-list"', toc)
        opf = z.read("OPS/package.opf").decode()
        self.assertNotIn("pageBreakMarkers", opf)


TWO_LEVEL = ("# Eins\n\nText.\n\n## Erster Abschnitt\n\nMehr.\n\n"
             "## Zweiter Abschnitt\n\nNoch mehr.\n")


class HierarchicalTOC(BuildCase):
    """## headings should appear as a nested level in the nav doc."""

    def test_h2_headings_nest_under_chapter_entry(self):
        z = self.build(chapters={"kapitel-eins.md": TWO_LEVEL})
        toc = z.read("OPS/TOC.xhtml").decode()
        self.assertIn(
            'href="s00000-kapitel-eins.xhtml#erster-abschnitt">Erster Abschnitt</a>',
            toc)
        # the section link is nested inside the chapter's <li>
        chapter_li = toc.index('s00000-kapitel-eins.xhtml">kapitel-eins</a>')
        section_link = toc.index("#erster-abschnitt")
        self.assertLess(chapter_li, section_link)
        self.assertIn("</ol></li>", toc.replace("\n", ""))

    def test_toc_depth_1_disables_nesting(self):
        z = self.build(chapters={"kapitel-eins.md": TWO_LEVEL},
                       extra={"toc_depth": 1})
        toc = z.read("OPS/TOC.xhtml").decode()
        self.assertNotIn("#erster-abschnitt", toc)


class ImageHandling(BuildCase):
    """Referenced GIFs must be bundled, not crash-and-drop."""

    def test_referenced_gif_lands_in_epub_without_conversion_errors(self):
        import contextlib, io
        img_dir = self.dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        Image.new("P", (10, 10)).save(img_dir / "anim.gif")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            z = self.build(chapters={
                "kapitel-eins.md": "# Eins\n\n![Eine Animation](anim.gif)\n"})
        self.assertIn("OPS/images/anim.gif", z.namelist())
        opf = z.read("OPS/package.opf").decode()
        self.assertIn('media-type="image/gif"', opf)
        self.assertNotIn("Failed to process", buf.getvalue())
        self.assertNotIn("Error processing image", buf.getvalue())


class DateValidation(BuildCase):
    """Non-W3CDTF dc:date should produce a warning (EPUB 3 wants W3CDTF)."""

    def test_bad_date_warns(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.build(metadata={"dc:date": "circa 2020"})
        self.assertIn("W3CDTF", buf.getvalue())

    def test_year_only_date_is_fine(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.build(metadata={"dc:date": "2020"})
        self.assertNotIn("W3CDTF", buf.getvalue())


class DescriptionSeeding(unittest.TestCase):
    """pdf2md should pre-fill description.json next to the markdown output."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument.new()
        pdf.new_page(200, 300)
        self.pdf_path = self.dir / "mein-buch.pdf"
        pdf.save(self.pdf_path)

    def seed(self, **kw):
        from modules import pdf2md
        return pdf2md.seed_description(self.dir, self.pdf_path, **kw)

    def test_seeds_description_with_fallbacks(self):
        self.seed(language="de")
        desc = json.loads((self.dir / "description.json").read_text())
        self.assertEqual(desc["metadata"]["dc:title"], "mein-buch")
        self.assertEqual(desc["metadata"]["dc:language"], "de")
        self.assertIn("chapters", desc)
        self.assertIn("accessibility", desc)

    def test_existing_description_is_not_overwritten(self):
        (self.dir / "description.json").write_text('{"metadata": {"dc:title": "Keep"}}')
        self.seed(language="de")
        desc = json.loads((self.dir / "description.json").read_text())
        self.assertEqual(desc["metadata"]["dc:title"], "Keep")


MD_WITH_IMAGES = """# Kapitel

Ein Absatz davor über Bindung.

![](abb1.jpeg)

Ein Absatz danach.

![Schon beschrieben](abb2.jpeg)

Und am Ende noch Text mit ![](images/unused/logo.png) im Fluss.
"""


class AltTextScanning(unittest.TestCase):
    """alt_text finds empty-alt refs, extracts context, applies drafts."""

    def test_finds_only_empty_alt_refs_outside_unused(self):
        from modules import alt_text
        found = alt_text.find_missing_alt(MD_WITH_IMAGES)
        self.assertEqual([m.path for m in found], ["abb1.jpeg"])

    def test_context_includes_surrounding_paragraphs(self):
        from modules import alt_text
        m = alt_text.find_missing_alt(MD_WITH_IMAGES)[0]
        self.assertIn("davor über Bindung", m.context)
        self.assertIn("danach", m.context)

    def test_apply_alt_rewrites_only_target_ref(self):
        from modules import alt_text
        m = alt_text.find_missing_alt(MD_WITH_IMAGES)[0]
        out = alt_text.apply_alt(MD_WITH_IMAGES, m, "Schema der Bindungsebenen")
        self.assertIn("![Schema der Bindungsebenen](abb1.jpeg)", out)
        self.assertIn("![Schon beschrieben](abb2.jpeg)", out)
        self.assertIn("![](images/unused/logo.png)", out)


class MarkerConfig(unittest.TestCase):
    """CLI options must map to the config keys marker 1.x actually reads."""

    def test_all_options_map_to_marker_keys(self):
        from modules import pdf2md
        cfg, llm = pdf2md.build_marker_config(
            max_pages=5, start_page=2, use_llm=True,
            llm_service="marker.services.claude.ClaudeService",
            paginate=True, no_images=True)
        self.assertEqual(cfg["page_range"], [2, 3, 4, 5, 6])
        self.assertIs(cfg["use_llm"], True)
        self.assertIs(cfg["paginate_output"], True)
        self.assertIs(cfg["extract_images"], False)
        self.assertEqual(llm, "marker.services.claude.ClaudeService")

    def test_defaults_are_empty(self):
        from modules import pdf2md
        cfg, llm = pdf2md.build_marker_config()
        self.assertEqual(cfg, {})
        self.assertIsNone(llm)

    def test_llm_service_ignored_without_use_llm(self):
        from modules import pdf2md
        cfg, llm = pdf2md.build_marker_config(
            llm_service="marker.services.claude.ClaudeService")
        self.assertIsNone(llm)
        self.assertNotIn("use_llm", cfg)


import re

from modules.mark2epub import get_TOC_XML, get_TOCNCX_XML

MD = ["a.md", "b.md", "c.md"]
TITEL = ["Erster", "Zweiter", "Dritter"]
GRUPPEN = {
    "a.md": ["1998", "1/1998", "Grundlagen"],
    "b.md": ["1998", "1/1998", "Praxis"],
    "c.md": ["1999", "1/1999", "Grundlagen"],
}


class TestVerschachtelteGruppen(unittest.TestCase):

    def test_toc_ohne_gruppen_bleibt_flach(self):
        xhtml = get_TOC_XML(["style.css"], MD, chapter_titles=TITEL, lang="de")
        self.assertNotIn("<span>", xhtml)
        # Nur die Haupt-TOC-Liste zählen; die separate Landmarks-Nav
        # enthält ebenfalls "<li><a" und ist hier nicht der Prüfgegenstand.
        toc_nav = xhtml.split('<nav epub:type="landmarks"')[0]
        self.assertEqual(toc_nav.count("<li><a"), 3)

    def test_toc_mit_gruppen_verschachtelt(self):
        xhtml = get_TOC_XML(["style.css"], MD, chapter_titles=TITEL, lang="de",
                            groups=GRUPPEN)
        self.assertIn("<span>1998</span>", xhtml)
        self.assertIn("<span>1/1998</span>", xhtml)
        self.assertIn("<span>Grundlagen</span>", xhtml)
        # 1998 und 1999 je einmal, 1/1998 einmal (zwei Kapitel darunter)
        self.assertEqual(xhtml.count("<span>1998</span>"), 1)
        self.assertEqual(xhtml.count("<span>1/1998</span>"), 1)
        self.assertEqual(xhtml.count("<span>Grundlagen</span>"), 2)

    def test_toc_oeffnende_und_schliessende_tags_sind_ausgeglichen(self):
        xhtml = get_TOC_XML(["style.css"], MD, chapter_titles=TITEL, lang="de",
                            groups=GRUPPEN)
        self.assertEqual(xhtml.count("<ol>"), xhtml.count("</ol>"))
        self.assertEqual(xhtml.count("<li>"), xhtml.count("</li>"))

    def test_ncx_bildet_die_hierarchie_ab(self):
        ncx = get_TOCNCX_XML(MD, uid="urn:issn:2191-2963", title="T",
                             chapter_titles=TITEL, lang="de", groups=GRUPPEN)
        self.assertIn("<text>1/1998</text>", ncx)
        self.assertEqual(ncx.count("<navPoint"), ncx.count("</navPoint>"))

    def test_ncx_setzt_dtb_depth_auf_die_echte_tiefe(self):
        ncx = get_TOCNCX_XML(MD, uid="u", title="T", chapter_titles=TITEL,
                             lang="de", groups=GRUPPEN)
        self.assertIn('name="dtb:depth" content="4"', ncx)

    def test_ncx_gruppenknoten_zeigt_auf_erstes_kind(self):
        ncx = get_TOCNCX_XML(MD, uid="u", title="T", chapter_titles=TITEL,
                             lang="de", groups=GRUPPEN)
        block = ncx[ncx.index("<text>1/1998</text>"):]
        treffer = re.search(r'<content src="([^"]+)"', block)
        self.assertIn("a.xhtml", treffer.group(1))

    def test_ncx_gleiches_ziel_gleiche_playorder(self):
        ncx = get_TOCNCX_XML(MD, uid="u", title="T", chapter_titles=TITEL,
                             lang="de", groups=GRUPPEN)
        paare = re.findall(r'playOrder="(\d+)"[^>]*>.*?<content src="([^"]+)"',
                           ncx, re.S)
        nach_ziel = {}
        for order, src in paare:
            nach_ziel.setdefault(src, set()).add(order)
        mehrdeutig = {s: v for s, v in nach_ziel.items() if len(v) > 1}
        self.assertEqual(mehrdeutig, {}, "RSC-005: gleiches Ziel, verschiedene playOrder")

    def test_ncx_playorder_steigt_monoton(self):
        ncx = get_TOCNCX_XML(MD, uid="u", title="T", chapter_titles=TITEL,
                             lang="de", groups=GRUPPEN)
        werte = [int(x) for x in re.findall(r'playOrder="(\d+)"', ncx)]
        self.assertEqual(werte, sorted(werte), f"nicht monoton: {werte}")

    def test_ncx_ids_bleiben_eindeutig(self):
        ncx = get_TOCNCX_XML(MD, uid="u", title="T", chapter_titles=TITEL,
                             lang="de", groups=GRUPPEN)
        ids = re.findall(r'<navPoint id="([^"]+)"', ncx)
        self.assertEqual(len(ids), len(set(ids)), "doppelte navPoint-id")


class TestKapitelSprache(unittest.TestCase):

    def test_kapitel_ohne_sprache_erbt_die_buchsprache(self):
        from modules.mark2epub import get_chapter_XML
        xhtml, _ = get_chapter_XML(".", "a.md", ["style.css"],
                                   content="# Titel\n", title="Titel", lang="de")
        self.assertIn('lang="de"', xhtml)
        self.assertIn('xml:lang="de"', xhtml)

    def test_kapitel_mit_eigener_sprache(self):
        from modules.mark2epub import get_chapter_XML
        xhtml, _ = get_chapter_XML(".", "b.md", ["style.css"],
                                   content="# Title\n", title="Title", lang="en")
        self.assertIn('lang="en"', xhtml)
        self.assertNotIn('lang="de"', xhtml)

    def test_beschreibung_mit_kapitelsprache_wird_durchgereicht(self):
        """Regression: chapter["language"] muss die Buchsprache überschreiben."""
        import json, tempfile, os, zipfile
        from modules.mark2epub import main as m2e_main
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "images"))
            with open(os.path.join(d, "de.md"), "w") as f:
                f.write("# Deutsch\n\nText.\n")
            with open(os.path.join(d, "en.md"), "w") as f:
                f.write("# English\n\nText.\n")
            with open(os.path.join(d, "style.css"), "w") as f:
                f.write("body{}")
            with open(os.path.join(d, "description.json"), "w") as f:
                json.dump({
                    "metadata": {"dc:title": "T", "dc:creator": "A",
                                 "dc:language": "de", "dc:identifier": "u"},
                    "default_css": ["style.css"],
                    "chapters": [
                        {"markdown": "de.md", "css": "style.css", "title": "Deutsch"},
                        {"markdown": "en.md", "css": "style.css", "title": "English",
                         "language": "en"},
                    ]}, f)
            m2e_main(["--yes", d, os.path.join(d, "out.epub")])
            with zipfile.ZipFile(os.path.join(d, "out.epub")) as z:
                en = [n for n in z.namelist() if n.endswith("en.xhtml")][0]
                self.assertIn(b'lang="en"', z.read(en))


class TestBildkompression(unittest.TestCase):

    def test_standard_bleibt_bei_1800(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from modules.mark2epub import copy_and_optimize_image
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "gross.jpeg"
            Image.new("RGB", (4000, 2000), (10, 20, 30)).save(src)
            dst = Path(d) / "out.jpeg"
            copy_and_optimize_image(src, dst)
            with Image.open(dst) as img:
                self.assertEqual(max(img.size), 1800)

    def test_parameter_werden_beachtet(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from modules.mark2epub import copy_and_optimize_image
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "gross.jpeg"
            img = Image.new("RGB", (4000, 2000))
            img.putdata([((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256)
                         for y in range(2000) for x in range(4000)])
            img.save(src)
            klein = Path(d) / "klein.jpeg"
            copy_and_optimize_image(src, klein, max_dimension=1200, quality=80)
            with Image.open(klein) as img:
                self.assertEqual(max(img.size), 1200)
            gross = Path(d) / "gross_out.jpeg"
            copy_and_optimize_image(src, gross, max_dimension=1200, quality=95)
            self.assertLess(os.path.getsize(klein), os.path.getsize(gross))


class KompressionImBau(BuildCase):
    """image_max_dimension/image_quality aus description.json muessen den
    Umweg ueber get_metadata_from_user ueberleben — sonst greift beim Bau
    ueber main() still die Voreinstellung 1800/85."""

    def _grosses_bild(self):
        img = Image.new("RGB", (2400, 1600))
        img.putdata([((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256)
                     for y in range(1600) for x in range(2400)])
        img.save(self.dir / "images" / "gross.jpeg")

    def test_konfigurierte_grenze_wirkt_im_gebauten_epub(self):
        (self.dir / "images").mkdir(parents=True, exist_ok=True)
        self._grosses_bild()
        z = self.build(chapters={"kapitel-eins.md": "# Eins\n\n![Muster](gross.jpeg)\n"},
                       extra={"image_max_dimension": 300, "image_quality": 60})
        with self.dir.joinpath("aus.jpeg").open("wb") as f:
            f.write(z.read("OPS/images/gross.jpeg"))
        with Image.open(self.dir / "aus.jpeg") as img:
            self.assertEqual(max(img.size), 300)

    def test_schluessel_bleiben_in_der_zurueckgeschriebenen_description(self):
        (self.dir / "images").mkdir(parents=True, exist_ok=True)
        self._grosses_bild()
        self.build(chapters={"kapitel-eins.md": "# Eins\n\n![Muster](gross.jpeg)\n"},
                   extra={"image_max_dimension": 300, "image_quality": 60})
        desc = json.loads((self.dir / "description.json").read_text(encoding="utf-8"))
        self.assertEqual(desc["image_max_dimension"], 300)
        self.assertEqual(desc["image_quality"], 60)


if __name__ == "__main__":
    unittest.main()
