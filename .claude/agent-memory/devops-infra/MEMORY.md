# DevOps Infra - pamięć projektu Monolynx

- [WAF Cloudflare blokuje MCP create_ticket/update_ticket z blokami kodu YAML](waf_mcp_ticket_yaml_block.md) - unikaj fence'ow ```yaml w opisie ticketu, opisuj mechanizm w prozie
- [Build-backend dla samodzielnych paczek w monorepo: hatchling](hatchling_standalone_packages.md) - sdk/ i cli/ używają hatchling, NIE setuptools/setuptools-scm (to tylko dla backendu w root pyproject.toml)
- [.gitlab-ci.yml: realna kolejność stage'y](gitlab_ci_stages_order.md) - build, lint, test, push, deploy (różni się od opisu w CLAUDE.md "lint -> test -> build")
- [Łańcuch fallbacku brancha bazowego/integracyjnego](base_branch_fallback_chain.md) - MONOLYNX_BASE_BRANCH -> MONOLYNX_MR_TARGET -> git symbolic-ref -> glab repo view; nigdy nie zgaduj "main" (MON-192, MON-197)
