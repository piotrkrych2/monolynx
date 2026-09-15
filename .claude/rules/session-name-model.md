---
paths:
  - "plugin/scripts/**"
  - "plugin/skills/**"
---

# Sesja Claude: jawny model i model na końcu nazwy

Każdy proces `claude`, który plugin startuje sam (`claude -p`, `claude --bg`), i każdy subagent (`Agent()`) ma model podany jawnie. Każda nazwa sesji (`--name`) kończy się tym modelem: `<slug>-<KEY>-<model>`, `<slug>-sprint-run-<model>`, `<slug>-sprint-end-<model>`, `<slug>-retro-<model>`.

## Rule

1. **Model jawny.** `claude -p` i `claude --bg` zawsze z `--model "${MONOLYNX_SPRINT_*MODEL:-opus}"` (tick i procesy `sprint_run.sh`: `MONOLYNX_SPRINT_TICK_MODEL`, sesje robocze ticketów: `MONOLYNX_SPRINT_MODEL`). `Agent()` w skillu zawsze z `model` wg sekcji "Model subagentow" skilla `work` / `work-simple` (wyjątek: developer, którego frontmatter ma `model:`).
2. **Model na końcu nazwy.** `--name "<nazwa>-<model>"`, z tą samą wartością, która idzie do `--model`. W `sprint_run.sh` dopisuje go wyłącznie `run_claude`; wywołujący podaje nazwę bez modelu.
3. **Dopasowanie po nazwie ignoruje sufiks modelu.** Kod, który szuka sesji po nazwie (`sprint_run_state.py` `match_session`), przyjmuje `<nazwa>` i `<nazwa>-<cokolwiek>`, nigdy równość z nazwą zawierającą bieżący model.
4. **Fable tylko świadomie**: przez nadpisanie zmiennej, dla roli koordynującej. Domyślna wartość w komendzie, skrypcie i dokumentacji to `opus` (albo `sonnet` dla subagentów wykonawczych).

## Pitfall

Proces bez `--model` bierze domyślny model konta, a subagent bez `model` dziedziczy model sesji, która go powołała. Oba zachowania są ciche: sprint działa poprawnie, tylko na najdroższym modelu, i widać to dopiero na rachunku. Nazwa bez modelu nie pozwala tego wyłapać w `claude agents`.

Dopasowanie po pełnej nazwie z modelem ma odwrotną pułapkę: po zmianie `MONOLYNX_SPRINT_MODEL` między tickami żywa sesja przestaje pasować, ticket wygląda na wolny i dostaje drugą sesję (duplikat, nieodwracalny).

## Regression

- 2026-09-14: sprint odpalony przez `sprint_run.sh` poszedł w całości na Fable 5.1 (domyślny model konta), razem z subagentami sesji `work`. Poprawka w pluginie 1.13.0 (MR !70).

## What to check

- Nowe albo zmienione wywołanie `claude -p` / `claude --bg` bez `--model` -> punkt 1.
- `--name` bez sufiksu modelu albo z sufiksem innym niż wartość `--model` -> punkt 2.
- `Agent(` w SKILL.md bez `model=` (poza developerem z `model:` we frontmatter) -> punkt 1.
- Porównanie `name == f"{slug}-{key}-{model}"` albo inne dopasowanie wymagające konkretnego modelu -> punkt 3.
- Domyślna wartość modelu `fable` w komendzie, skrypcie albo tabeli -> punkt 4.
