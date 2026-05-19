---
name: segmented-control-filter-bar
description: Wzorzec paska filtrów z segmented controls dla statusu/sortowania w modułach Monolynx
metadata:
  type: project
---

## Segmented control — pasek filtrów

Stosowany w `issues.html` (500ki) i wzorcem do naśladowania w innych modułach.

### Struktura HTML

```html
<div class="inline-flex rounded-md overflow-hidden border border-gray-600">
    <a href="?status=<val>&sort=<sort>&order=<order>"
       class="px-3 py-1.5 text-sm font-medium transition
              {% if active == val %}bg-indigo-600 text-white{% else %}bg-gray-700 text-gray-300 hover:bg-gray-600{% endif %}">
        Etykieta
    </a>
    ...
</div>
```

### Zasady

1. Każdy link przekazuje WSZYSTKIE query params (status + sort + order) — zmiana jednego nie resetuje pozostałych.
2. Aktywna opcja: `bg-indigo-600 text-white`. Nieaktywna: `bg-gray-700 text-gray-300 hover:bg-gray-600`.
3. Separator między grupami: `<div class="h-5 w-px bg-gray-600 hidden sm:block"></div>`.
4. Kontener paska: `bg-gray-800 border border-gray-700 rounded-lg p-3 mb-4 flex flex-wrap items-center gap-3`.

### Defensywny fallback w Jinja2

Backend może nie przekazać `filters` — zabezpieczenie:

```jinja2
{% set _status = filters.status if filters is defined and filters.status is defined else "unresolved" %}
{% set _sort   = filters.sort   if filters is defined and filters.sort   is defined else "last_seen" %}
{% set _order  = filters.order  if filters is defined and filters.order  is defined else "desc" %}
```

Etykiety z fallbackiem:
```jinja2
{% if issue_status_labels is defined %}
  {% set _status_labels = issue_status_labels %}
{% else %}
  {% set _status_labels = {"all": "Wszystkie", "unresolved": "Nierozwiązane", ...} %}
{% endif %}
```

### Toggle kierunku — SVG ikony

`desc` → strzałka w dół (`M19 9l-7 7-7-7`)
`asc`  → strzałka w górę (`M5 15l7-7 7 7`)

```html
<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24"
     stroke="currentColor" stroke-width="2">
    <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
</svg>
```

### Pusty stan z filtrem aktywnym

```jinja2
{% if _status != "all" %}
<p class="text-lg">Brak issues w wybranym statusie</p>
<p class="text-sm mt-2">... <a href="?status=all&sort={{ _sort }}&order={{ _order }}" ...>pokaż wszystkie</a></p>
{% else %}
<p class="text-lg">Brak błędów</p>
{% endif %}
```
