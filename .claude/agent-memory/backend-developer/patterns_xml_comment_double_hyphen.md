---
name: patterns-xml-comment-double-hyphen
description: XML comments cannot contain "--" anywhere except the closing "-->" - breaks fixtures that embed shell commands with CLI flags
metadata:
  type: project
---

Fixture'y XML z nagłówkiem proweniencji jako komentarz (`<!-- captured: <dokładna komenda> | ... -->`) łatwo łamią specyfikację XML, jeśli przechwycona komenda zawiera flagi CLI z podwójnym myślnikiem (`--reportDir`, `--targetClasses` itd.). XML zabrania sekwencji `"--"` wewnątrz komentarza poza końcowym `-->`. `xml.etree.ElementTree.fromstring()` rzuca wtedy `ParseError: not well-formed (invalid token)` na całym pliku, nie tylko na komentarzu - parser nigdy nie dochodzi do właściwego roota.

**Why:** MON-149 (parser raportów PIT/pit-xml) - `tests/fixtures/pitest/mutations.1.30.0.xml` i `mutations-clean.1.30.0.xml` miały siedem wystąpień `--` w komentarzu proweniencji (komenda `java ... --reportDir report --targetClasses ...`), co czyniło oba fixture'y niesparsowalnymi dla każdego zgodnego parsera XML, niezależnie od poprawności właściwego kodu parsującego. Objaw: happy-path testy dla formatu XML failują z `ParseError` mimo poprawnej logiki `_parse_pit_xml`.

**How to apply:** Przy pisaniu LUB recenzowaniu fixture'u XML z komentarzem proweniencji zawierającym dosłowną komendę CLI - sprawdź `grep -c -- '--' <plik>` na treści komentarza (wyłączając zamykające `-->`). Jeśli komenda ma flagi z podwójnym myślnikiem, komentarz trzeba przeformułować (np. pojedynczy myślnik w opisie, albo owinięcie komendy w encję/inny separator) zanim fixture trafi do testów. To defekt fixture'u, nie parsera - nie naprawiaj go w kodzie parsującym, zgłoś do właściciela fixture'u (qa-tester/fixture-capture). Zobacz [[patterns_mcp_compact_output]] dla innych wzorców granic-systemu w tym repo.
