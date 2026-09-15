---
paths:
  - "plugin/.claude-plugin/plugin.json"
  - "plugin/README.md"
---

# Wersja pluginu: bump przy merge, nie w tickecie

Tickety sprintu pracują równolegle (dyspozytor `sprint-run`), a każdy z nich zmienia plugin. Gdy każdy bumpuje `plugin/.claude-plugin/plugin.json` i dopisuje `### X.Y.Z` do changelogu we własnym branchu, każdy merge do `main` kończy się konfliktem w dwóch plikach i ręcznym przenumerowaniem (sprint "Testy mutacyjne w pluginie": pięć ticketów, pięć kolizji, wersje 1.8.2 -> 1.12.0 nadawane po fakcie).

## Rule

1. **Ticket dopisuje swój wpis pod nagłówkiem `### Unreleased`** na początku sekcji `## Changelog` w `plugin/README.md` (tworzy go, jeśli nie ma), z kluczem ticketu w pierwszym punkcie. Nie zmienia `plugin/.claude-plugin/plugin.json`.
2. **Numer i bump nadaje ten, kto merguje do `main`** (człowiek, sesja mr-prep albo `sprint-end`): zamienia `### Unreleased` na `### X.Y.Z` (minor przy nowym skillu/hooku/skrypcie, patch przy poprawce), ustawia tę samą wartość w `plugin.json`, w tym samym commicie.
3. Wyjątek: ticket, który sam jest wydaniem (hotfix mergowany od razu, poza sprintem) może nadać numer, jeśli w chwili commita jest jedynym otwartym MR-em zmieniającym plugin.
4. Do czasu wdrożenia obsługi `### Unreleased` w `scripts/validate_plugin_consistency.py` (ticket z retro) walidator wymaga równości wersji z pierwszym wpisem: ticket, który nie może zostawić `Unreleased`, wybiera numer o jeden wyżej niż najwyższy numer widoczny na `main` **i** we wszystkich otwartych MR-ach (`glab mr list`), i pisze to w komentarzu planu.

## Pitfall

Dwa branche na tym samym `main` niezależnie wybierają "1.10.0". Oba pipeline'y są zielone, oba MR-y wyglądają na gotowe, a merge drugiego wymaga ręcznego przesunięcia całego wpisu changelogu i podbicia liczników skilli. Konflikt nie jest widoczny do momentu merge.

## Regression

- MON-149 (1.9.0), MON-150 (1.10.0), MON-151 (1.10.0), MON-152 (1.11.0), MON-166 (1.8.2, zajęte przez MON-173/174): pięć kolizji `plugin.json` + changelog przy merge, rozwiązywane przez osobną sesję `monolynx-mr-prep` (2026-09-13).

## What to check

- Diff ticketu zmienia `plugin.json` `version` w sprincie z innymi otwartymi MR-ami pluginu -> zamiast tego `### Unreleased` (albo pkt 4, jawnie w planie).
- Wpis changelogu bez klucza ticketu w pierwszym punkcie -> nie da się go przypisać przy scalaniu.
- Merge do `main` zostawia `### Unreleased` jako pierwszy wpis -> mergujący nie nadał numeru.
