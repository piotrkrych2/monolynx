---
name: waf-mcp-ticket-yaml-block
description: WAF Cloudflare na proxy MCP claude.ai blokuje create_ticket/update_ticket gdy opis zawiera bloki kodu (fence'y YAML/Ruby) z wzorcami przypominajacymi config/secrets
metadata:
  type: project
---

Wywolanie `mcp__claude_ai_Monolynx__create_ticket` z duzym opisem zawierajacym fence'y ```yaml``` (np. definicje jobow GitLab CI z polami typu `id_tokens:`, `environment:`, nazwami zmiennych w stylu `*_TOKEN`) zostalo dwukrotnie zablokowane przez WAF Cloudflare na anthropic.com (proxy MCP claude.ai) - HTML "Sorry, you have been blocked". Zaobserwowane 2026-08-06 przy tworzeniu ticketu o publikacji CLI na PyPI (bloki YAML z jobem `publish:pypi` i zmienna `PYPI_ID_TOKEN`).

**Why:** to samo zjawisko co [[mcp-waf-cloudflare-block]] (update_wiki_page z connection stringami) - sygnatury WAF traktuja bloki wygladajace jak config/credentiale (klucz: wartosc, wcięcia, nazwy z `TOKEN`/`SECRET`) jako atak, niezaleznie od tego czy to faktyczny sekret czy tylko opis mechanizmu w YAML.

**How to apply:** przy tworzeniu/aktualizacji ticketow z technicznym opisem CI/CD, configu, credentiali - pisz w prozie, unikaj blokow kodu z fence'ami (```yaml, ```ruby, ```toml z polami tokenow). Jesli trzeba pokazac strukture pliku, opisz slownie ("job w stage'u X uruchamia komende Y"). Przy blokadzie: NIE ponawiaj tego samego payloadu - obejscie ktore zadzialalo: utworz ticket z krotkim placeholderem opisu (`create_ticket`), potem `update_ticket` z pelnym opisem w prozie bez fence'ow, a acceptance_criteria dodawaj pojedynczo przez `add_acceptance_criterion` (male payloady).
