---
name: public-standalone-pages
description: Wzorzec publicznych, anonimowych stron (bez sesji, bez sidebara) - dziedziczą z layouts/landing.html, NIE są standalone
metadata:
  type: project
---

## Publiczne strony treściowe (np. `/blog/{slug}`, `/features/*`, `/contact`)

Błędne pierwsze podejście: pisanie w pełni samodzielnego dokumentu HTML (własny `<html>/<head>/<body>`) dla
route'ów bez sesji. Poprawny wzorzec w tym projekcie jest inny — **wszystkie publiczne strony treściowe
dziedziczą z `layouts/landing.html`**, nie z `layouts/base.html` (dashboard, wymaga sesji) i nie standalone.
Przykłady do naśladowania: `features/feature.html`, `contact.html`.

`layouts/landing.html` dostarcza: GA tracking (`partials/_google_analytics.html`), favicony/manifest, pełny
zestaw Open Graph / Twitter jako **blocki** (`{% block og_title %}`, `og_description`, `og_url`, `og_image`,
`twitter_title`, `twitter_description`, `twitter_image`), navbar (fixed top-0, z przełącznikiem language/login)
i stopkę. `{% block title %}`, `{% block meta_description %}`, `{% block head %}` (do dorzucenia np.
`<link rel="canonical">` lub własnego `<style>`), `{% block content %}`.

### Kluczowe pułapki

1. **Brak typography plugin** — `layouts/landing.html` ładuje `https://cdn.tailwindcss.com` BEZ
   `?plugins=typography`. Klasa `prose`/`prose-invert` nic nie zrobi. Dla renderowanej treści markdown trzeba
   napisać jawny `<style>` z regułami per-selektor (`.blog-content h2`, `ul`, `pre`, `code`, `table`...) w
   `{% block head %}`, każda reguła w wariancie jasnym + `.dark .blog-content ...` (bez `dark:` variant, bo to
   zwykły CSS, nie klasy Tailwind).
2. **Motyw przełączalny, nie tylko ciemny** — `body` w layoucie ma `bg-white dark:bg-gray-950
   text-gray-900 dark:text-gray-100`. Każda klasa Tailwind w treści MUSI mieć oba warianty
   (`text-gray-900 dark:text-white`), nigdy tylko ciemny (`text-white` samo w sobie łamie jasny motyw).
3. **Navbar jest `fixed top-0`** — kontener treści potrzebuje `pt-32` na głównej sekcji (wzorzec z
   `feature.html`/`contact.html`), inaczej navbar zasłania nagłówek strony.
4. **`og:type` jest zahardkodowany** na `website` w layoucie i NIE jest blockiem — nie dodawać własnego
   duplikatu tagu, nawet dla treści typu artykuł.
5. **`canonical`** nie istnieje w layoucie — dodać ręcznie w `{% block head %}`.
6. Linki wewnętrzne w treści publicznej (breadcrumbs/children/backlinks) idą do tej samej publicznej
   przestrzeni (np. `/blog/{{ slug }}`), NIE do `/dashboard/{{ project.slug }}/wiki/...`.

Przykład: [[public-blog-page-MON-201]] — `src/monolynx/templates/blog/page.html`.

### Kontrakt kontekstu z backendem

Gdy frontend i backend rozwijane równolegle przez różnych agentów, backend dostarcza dokładnie nazwany zestaw
zmiennych (np. `content_html` już zsanityzowany przez `nh3` — jedyne miejsce z `| safe`). Reszta zmiennych
(tytuły w breadcrumbs/children/backlinks) NIE dostaje `| safe` — normalne autoescaping Jinja2 wystarcza i jest
wymagane dla bezpieczeństwa. `lang` i `app_url` są konsumowane bezpośrednio przez `layouts/landing.html` —
child template nie musi ich osobno obsługiwać w `<head>`.
