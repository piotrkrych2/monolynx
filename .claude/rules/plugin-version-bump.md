---
paths:
  - "plugin/.claude-plugin/plugin.json"
  - "plugin/README.md"
---

# Wersja pluginu: bump przy merge, nie w tickecie

Tickety sprintu pracują równolegle (dyspozytor `sprint-run`), a każdy z nich zmienia plugin. Gdy każdy bumpuje `plugin/.claude-plugin/plugin.json` i dopisuje `### X.Y.Z` do changelogu we własnym branchu, każdy merge do `main` kończy się konfliktem w dwóch plikach i ręcznym przenumerowaniem (sprint "Testy mutacyjne w pluginie": pięć ticketów, pięć kolizji, wersje 1.8.2 -> 1.12.0 nadawane po fakcie).

## Rule

1. **Ticket dopisuje swój wpis pod nagłówkiem `### Unreleased`** na początku sekcji `## Changelog` w `plugin/README.md` (tworzy go, jeśli nie ma). Każdy punkt najwyższego poziomu zaczyna się od klucza ticketu (`- MON-123: opis`), wcięte kontynuacje są bez klucza. Nie zmienia `plugin/.claude-plugin/plugin.json` ani `plugin/.codex-plugin/plugin.json`. `work` i `work-simple` wklejają tę zasadę do promptu każdego developera (blok `changelog-unreleased`).
2. **Numer i bump nadaje ten, kto merguje**. Tickety mergowane do brancha integracyjnego (`MONOLYNX_MR_TARGET`, tryb dyspozytora): `/monolynx:sprint-end`, job `version-bump` (4c), na branchu integracyjnym przed jego merge do `main`, więc `main` nie widzi `### Unreleased`. Ticket mergowany wprost do `main`: mergujący na branchu MR, przed merge (człowiek; `mr-queue` tego nie robi), a `sprint-end` na `main` jest tylko siatką po merge bez numeru. Zamienia `### Unreleased` na `### X.Y.Z` (minor przy nowym skillu/hooku/skrypcie od ostatniego wydania, patch przy poprawce) i ustawia tę samą wartość w obu manifestach, w tym samym commicie.
3. Wyjątek: ticket, który sam jest wydaniem (hotfix mergowany od razu, poza sprintem) może nadać numer, jeśli w chwili commita jest jedynym otwartym MR-em zmieniającym plugin. Decyzję podejmuje Team Manager i zapisuje ją w komentarzu planu.

## Właściciel wpisu `### Unreleased`

Team Manager, który przydziela zmianę w `plugin/`, dopisuje `plugin/README.md` do przydziału plików jednego developera (tego, który zmienia plugin) albo zapisuje w planie, że wpis robi sam. Developer bez `plugin/README.md` w przydziale nie edytuje go, tylko zgłasza `STOP:`. Wszystkie punkty jednego ticketu stoją pod `### Unreleased` razem, bez wpisów innego ticketu pomiędzy.

## Pitfall

Dwa branche na tym samym `main` niezależnie wybierają "1.10.0". Oba pipeline'y są zielone, oba MR-y wyglądają na gotowe, a merge drugiego wymaga ręcznego przesunięcia całego wpisu changelogu i podbicia liczników skilli. Konflikt nie jest widoczny do momentu merge.

## Regression

- MON-149 (1.9.0), MON-150 (1.10.0), MON-151 (1.10.0), MON-152 (1.11.0), MON-166 (1.8.2, zajęte przez MON-173/174): pięć kolizji `plugin.json` + changelog przy merge, rozwiązywane przez osobną sesję `monolynx-mr-prep` (2026-09-13).
- MON-191: czerwony `plugin-consistency`, numer wersji nadany po fakcie.
- MON-197: błędną decyzję Team Managera w sprawie wersji zatrzymał dopiero STOP technical-writera.
- MON-123: `plugin/README.md` nie był w przydziale backend-developera; STOP rozstrzygał TM w trakcie pracy.
- MON-178: punkty pod `### Unreleased` bez klucza ticketu, poprawione przez TM pod walidator z MON-177.
- MON-200: wpis MON-200 wstawiony w środek grupy MON-177 pod `### Unreleased` (-5).

## What to check

- Diff ticketu zmienia `version` w którymkolwiek manifeście w sprincie z innymi otwartymi MR-ami pluginu -> zamiast tego `### Unreleased` (wyjątek tylko wg pkt 3, jawnie w planie).
- Plan TM przydziela zmianę w `plugin/` bez właściciela wpisu `### Unreleased` (sekcja „Właściciel wpisu”).
- Wpisy jednego ticketu pod `### Unreleased` rozdzielone wpisem innego ticketu.
- Punkt pod `### Unreleased` bez klucza ticketu, pusta sekcja `### Unreleased` albo `### Unreleased` nie na pierwszej pozycji -> `scripts/validate_plugin_consistency.py` zgłasza to w każdym trybie (job `plugin-consistency` na MR).
- Merge do `main` zostawia `### Unreleased` jako pierwszy wpis -> mergujący nie nadał numeru; walidator w trybie wydania (`--release` albo `CI_COMMIT_BRANCH=main`) zgłasza "wydanie bez numeru" i pipeline `main` jest czerwony do merge wydania (`sprint-end`, job `version-bump`).
