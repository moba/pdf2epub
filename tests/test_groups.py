

# --- Einzelkind-Gruppen falten ---------------------------------------------
#
# Die Fixtures brauchen echte Geschwister auf jeder Ebene. Ein Baum mit nur
# einem Jahrgang und nur einem Heft faltet vollstaendig zusammen — richtig,
# aber als Probe wertlos.


def test_einzige_rubrik_unter_einem_heft_wird_entfernt():
    """37 von 44 Heften tragen nur "Beitraege"; dort ist die Ebene Verpackung."""
    from modules.mark2epub import _gruppenbaum
    groups = {"a.md": ["1998", "1/1998", "Beiträge"],
              "b.md": ["1998", "1/1998", "Beiträge"],
              "c.md": ["1998", "2/1998", "Beiträge"]}
    baum = _gruppenbaum(["a.md", "b.md", "c.md"], groups)
    assert [p for p, _, _ in baum] == [["1/1998"], ["1/1998"], ["2/1998"]]


def test_echte_rubriken_bleiben_erhalten():
    """Wo mehrere Rubriken nebeneinander stehen, traegt die Ebene Information."""
    from modules.mark2epub import _gruppenbaum
    groups = {"a.md": ["2016", "2/2016", "FAMILIENWELTEN"],
              "b.md": ["2016", "2/2016", "AUS DER PRAXIS"],
              "c.md": ["2016", "1/2016", "Beiträge"]}
    baum = _gruppenbaum(["a.md", "b.md", "c.md"], groups)
    assert [p for p, _, _ in baum] == [["2/2016", "FAMILIENWELTEN"],
                                       ["2/2016", "AUS DER PRAXIS"],
                                       ["1/2016"]]


def test_faltung_wirkt_je_zweig_getrennt():
    """Ein Heft mit echten Rubriken hindert ein anderes nicht am Falten."""
    from modules.mark2epub import _gruppenbaum
    groups = {"a.md": ["2015", "1/2015", "Beiträge"],
              "b.md": ["2016", "2/2016", "FAMILIENWELTEN"],
              "c.md": ["2016", "2/2016", "AUS DER PRAXIS"]}
    baum = _gruppenbaum(["a.md", "b.md", "c.md"], groups)
    # Beide Jahrgaenge verlieren ihre einzige Heftebene; 2016 behaelt seine
    # beiden Rubriken, 2015 verliert die einzelne.
    assert [p for p, _, _ in baum] == [["2015"],
                                       ["2016", "FAMILIENWELTEN"],
                                       ["2016", "AUS DER PRAXIS"]]


def test_alles_gleich_faltet_vollstaendig():
    from modules.mark2epub import _gruppenbaum
    groups = {"a.md": ["Teil", "1998", "1/1998", "Beiträge"]}
    assert [p for p, _, _ in _gruppenbaum(["a.md"], groups)] == [[]]


def test_kapitel_ohne_gruppe_verhindert_die_faltung():
    """Endet ein Kapitel auf der Ebene, ist die Gruppe ein echter Nachbar."""
    from modules.mark2epub import _gruppenbaum
    groups = {"a.md": [], "b.md": ["Beiträge"]}
    baum = _gruppenbaum(["a.md", "b.md"], groups)
    assert [p for p, _, _ in baum] == [[], ["Beiträge"]]
