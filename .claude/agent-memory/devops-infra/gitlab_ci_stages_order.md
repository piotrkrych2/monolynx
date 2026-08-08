---
name: gitlab-ci-stages-order
description: Realna kolejnosc stage'y w .gitlab-ci.yml (root) rozni sie od opisu w CLAUDE.md
metadata:
  type: project
---

CLAUDE.md opisuje pipeline jako "lint -> test -> build (main only) -> deploy (manual)". Stan faktyczny `.gitlab-ci.yml` (sprawdzony 2026-08-06): `stages: [build, lint, test, push, deploy]`. Kluczowe joby: `docker-build-test` (stage build, MR/develop/main), `clean-code` (stage lint, ruff+mypy+pytest z coverage), `trivy-scan`/`dockle-scan` (stage test, tylko MR), `docker-push-develop`/`docker-push-prod` (stage push), `deploy-staging` (manual), `sync-graph` (graphify, allow_failure, tylko main), `wiki-post-merge` (tylko main). Wszystkie istniejace joby dotycza obrazu Dockera backendu - katalog `cli/` (paczka CLI, sprint "CLI monolynx - fundament + REST API v2", sierpien 2026) nie ma jeszcze wlasnych jobow lint/test/publish w tym pliku.

**Why:** CLAUDE.md to opis ogolny/historyczny, nie zawsze aktualny co do dokladnej kolejnosci - przy pracy nad CI zawsze czytaj plik zamiast polegac na CLAUDE.md.

**How to apply:** przy dodawaniu nowych jobow (np. publish:pypi dla CLI) sprawdz aktualny plik przed zalozeniem gdzie w kolejnosci stage'y ma trafic nowy stage.
