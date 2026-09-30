# DevOps Infra - pamięć projektu Monolynx

- [WAF Cloudflare blokuje MCP create_ticket/update_ticket z blokami kodu YAML](waf_mcp_ticket_yaml_block.md) - unikaj fence'ow ```yaml w opisie ticketu, opisuj mechanizm w prozie
- [Build-backend dla samodzielnych paczek w monorepo: hatchling](hatchling_standalone_packages.md) - sdk/ i cli/ używają hatchling, NIE setuptools/setuptools-scm (to tylko dla backendu w root pyproject.toml)
- [.gitlab-ci.yml: realna kolejność stage'y](gitlab_ci_stages_order.md) - build, lint, test, push, deploy, publish (różni się od opisu w CLAUDE.md "lint -> test -> build"); publish dodany w MON-136 (tylko job PyPI na tagu cli-v*)
- [Łańcuch fallbacku brancha bazowego/integracyjnego](base_branch_fallback_chain.md) - MONOLYNX_BASE_BRANCH -> MONOLYNX_MR_TARGET -> git symbolic-ref -> glab repo view; nigdy nie zgaduj "main" (MON-192, MON-197)
- [PyPI Trusted Publishing w GitLab CI + format license vs PEP 639](pypi_trusted_publishing.md) - nazwa zmiennej id_tokens = `<AUD>_ID_TOKEN` uppercase (PYPI_ID_TOKEN / TESTPYPI_ID_TOKEN, NIE jedna stała nazwa), twine>=6.1.0 wymienia token sam, license={text=...} nie SPDX-string gdy trzeba zachować klasyfikator License :: (MON-136)
- [Homebrew: update-python-resources / style / audit gotchas](homebrew_update_python_resources.md) - wymaga formuly w tapie, --extra-packages comma-separated, python@3.x keg-only, obraz homebrew/brew bez python3, Landlock w zagniezdzonej VM (MON-138)

## Reguly z review krytyka

- Komentarz w pliku opisuje automatyzację, która istnieje w repo w tym samym diffie, a nie tę, która dopiero ma powstać (MON-138).
- Gdy stan repo zmienia się w trakcie ticketu, komentarze opisujące "czego jeszcze nie ma" grepujesz i aktualizujesz przed oddaniem (MON-138).
