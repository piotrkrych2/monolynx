---
name: patterns-dependency-badge
description: Wzorzec współdzielonego partiala-badge (np. "zablokowany") osadzanego w wielu widokach przez {% include %} bez {% with %}
metadata:
  type: project
---

MON-199 (zależności ticketów w Scrum): badge "zablokowany" (`templates/dashboard/scrum/_blocked_badge.html`)
jest osadzany przez `{% include "dashboard/scrum/_blocked_badge.html" %}` w trzech miejscach:
`ticket_detail.html` (obok `<h1>`), `backlog.html` i `board.html` (w pętli po ticketach).

**Why:** `{% include %}` w Jinja2 domyślnie dziedziczy kontekst rodzica (chyba że `without context`).
Skoro zmienna pętli we wszystkich trzech miejscach nazywa się `ticket`, partial może wprost odwoływać się
do `ticket.blocked_by` bez potrzeby `{% with ticket=... %}`. To tańsze niż makro (brak importu) i czytelniejsze
niż kopiowanie znacznika w 3 plikach.

**How to apply:** przy dodawaniu współdzielonego badge/etykiety renderowanej w wielu widokach na tej samej
zmiennej pętli — najpierw sprawdź nazwę zmiennej pętli w każdym miejscu użycia (`grep "{% for.*in"` w docelowych
plikach); jeśli się różni, trzeba `{% with local_name=inne_nazwa %}{% include ... %}{% endwith %}`.

Logika widoczności badge: `{% set active = coś.blocked_by | rejectattr("status", "equalto", "done") | list %}`
+ `{% if active %}` — wzorzec spójny z resztą modułu Scrum (por. `ticket.acceptance_criteria | selectattr(...)`
w `ticket_detail.html`).

Gotcha: taki partial wymaga, żeby backend eager-loadował relację (`selectinload(Ticket.blocked_by)`) na KAŻDYM
query, które przekazuje obiekty do widoku zawierającego include — łatwo przeoczyć jeden endpoint (np. detail),
skoro list-view queries (backlog/board) już to mają. Zobacz [[db-commit]] po analogii — brakujący eager load
w async SQLAlchemy + Jinja sync render = `MissingGreenlet`, nie SyntaxError, więc test integracyjny to złapie
dopiero na realnym query, nie na `env.parse()`.
