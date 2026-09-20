---
name: base-branch-fallback-chain
description: Lancuch fallbacku do ustalania brancha bazowego/integracyjnego w skillach pluginu (MON-192, MON-197) - kolejnosc i miejsca uzycia
metadata:
  type: project
---

Plugin Monolynx ustala branch bazowy/integracyjny tym samym lancuchem fallbacku w kilku miejscach: `MONOLYNX_BASE_BRANCH` (env/config) -> `MONOLYNX_MR_TARGET` (env/config, MON-192, branch integracyjny MR) -> `git symbolic-ref refs/remotes/origin/HEAD` (wynik `refs/remotes/origin/<nazwa>`) -> `glab repo view -F json --jq .default_branch`. Brak wyniku na koncu lancucha: NIGDY nie zgaduj `main` - repo moze uzywac `master`/`develop`/innej nazwy.

**Why:** literal `git checkout main` w skillach (`work`, wiki-ingest/init/sync-merge) psul sie na projektach z inna galezia glowna. MON-192 wprowadzil `MONOLYNX_MR_TARGET` dla brancha integracyjnego (cel MR); MON-197 rozszerzyl na branch bazowy roboczy (`MONOLYNX_BASE_BRANCH`) i na bramke zapisu wiki, uzywajac tego samego lancucha zamiast duplikowac logike.

**How to apply:** kazde nowe miejsce w skillu/skrypcie, ktore dzis ma zaszyty `main`/`master`, powinno przejsc na ten sam 4-elementowy lancuch. W SKILL.md kazda komenda lancucha idzie OSOBNO (bez `$VAR`/`$(...)` - sesja w worktree je odrzuca), wynik podstawiany literalnie do kolejnej. W Pythonie (np. `plugin/scripts/sprint_run_tick.py::_resolve_base_branch`) kazdy krok owinac w try/except na `_GitError`/`subprocess` - brak `glab` w PATH albo brak zdalnego HEAD (klon bez `git remote set-head origin -a`) sa normalnym, nieblokujacym wynikiem, nie wyjatkiem, ktory ma wywrocic caly proces.

Zwiazane: [[gitlab_ci_stages_order]] (inna czesc konfiguracji CI tego projektu).
