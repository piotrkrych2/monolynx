---
name: wiki-llm-method
description: Metoda LLM Wiki (wg Karpathy'ego) w Monolynx - regulamin, strony systemowe, narzedzia MCP, skille
metadata:
  type: project
---

Metoda LLM Wiki (wg Andreja Karpathy'ego): wiki jako narastajacy artefakt wiedzy, agent AI pisze/utrzymuje, czlowiek dostarcza zrodla i pyta.

**Why:** ticket MON-73, 4 warstwy (modele/serwisy/narzedzia MCP, potem skille+plugin = warstwa 4 technical-writera).
**How to apply:** przy dokumentowaniu lub edycji skilli wiki trzymaj sie spojnosci z regulaminem.

## Regulamin (stala)
- `src/monolynx/services/wiki_templates.py` -> `DEFAULT_WIKI_SCHEMA` (pelny regulamin, PL). To zrodlo prawdy dla skilli.
- Zapisuje sie jako strona o slugu `wiki-schema` przy bootstrapie. Edytowalny (wspolewoluuje).

## Typy stron (frontmatter `type`)
encja | koncept | źródło | synteza. Frontmatter: type, status (aktywna|szkic|przestarzała), ostatni_przeglad (YYYY-MM-DD), tagi.
Summary = pierwsza nie-naglowkowa linia (trafia do wiki-index). Linkowanie: ZAWSZE wikilink ze slugiem, nigdy pelny URL.
Marker sprzecznosci DOKLADNIE: `> **Sprzeczność [YYYY-MM-DD]:** ...` (tak wykrywa lint_wiki).

## Strony systemowe (slug z prefiksem wiki-, bo SLUG_PATTERN zabrania `_`)
wiki-index (katalog, auto), wiki-log (dziennik append-only), wiki-schema (regulamin, edytowalny).
NIE uzywaj starego zapisu `_index`/`_log`/`_wiki-schema` z opisu ticketu.

## Narzedzia MCP (gating: project.wiki_llm_enabled)
- bez flagi: get_wiki_config, set_wiki_config, bootstrap_wiki_llm
- wymagaja flagi (blad "Metoda LLM Wiki jest wyłączona..."): lint_wiki, get_wiki_backlinks, regenerate_wiki_index, append_wiki_log
- create_wiki_page/update_wiki_page NIE maja parametru type/slug/status - typ przez frontmatter, slug auto z title.
- get_wiki_config zwraca: wiki_llm_enabled, index_page_id, log_page_id, schema_page_id (null gdy brak strony).
- lint_wiki zwraca: orphans, dead_links, contradictions, gaps.

## Skille (warstwa 4)
3 nowe: wiki-init (bootstrap), wiki-ingest (INGEST), wiki-lint (LINT). search rozszerzony o Krok 5 QUERY (zapis syntezy).
Plugin: 7 -> 10 skilli. Wersja pluginu 1.1.0 -> 1.1.1.
