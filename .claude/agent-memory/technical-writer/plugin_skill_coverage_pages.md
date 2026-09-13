---
name: plugin-skill-coverage-pages
description: Nowy skill pluginu trzeba dopisac na 6 stronach pokrycia + recznie podbic licznik skilli w 4 miejscach (walidator licznika NIE sprawdza)
metadata:
  type: project
---

Dodanie nowego skilla `plugin/skills/<nazwa>/` wymaga wpisu `/monolynx:<nazwa>` na
**wszystkich** stronach z `DEFAULT_PAGES` w `scripts/validate_plugin_consistency.py`
(`check_skill_coverage`): `plugin/README.md`, `plugin/skills/help/SKILL.md`,
`templates/dashboard/profile/mcp_guide.html`, `static/agent-bootstrap.md`,
`static/how-to-use-monolynx.md`, `src/monolynx/main.py`.

Osobno, **poza zasiegiem walidatora**, trzeba recznie podbic licznik skilli w 4 miejscach:
`main.py` (EN "all N ticket and wiki skills" + PL "wszystkie N skilli"),
`agent-bootstrap.md` ("## Komplet N skilli"), `how-to-use-monolynx.md` ("Full set of N skills"),
`mcp_guide.html` ("Dostajesz N skilli").

**Why:** walidator pilnuje tylko obecnosci nazwy skilla, nie liczby. Rozjechany licznik
przechodzi CI i zostaje w UI oraz na landingu.

**How to apply:** przy kazdym nowym skillu odpal
`grep -rn "N skill\|Komplet N\|Dostajesz N\|set of N" src/monolynx/` ze stara liczba,
zanim uznasz prace za skonczona. Podzial rol w sprincie: skill-writer bierze
`plugin/README.md` + `plugin/skills/help/SKILL.md`, technical-writer pozostale 4 strony,
wiec walidator jest czerwony do czasu zejscia sie obu polowek. Patrz tez
[[plugin_docs_conventions]].
