# Frontend Developer — Agent Memory

## Wzorce komponentów
- [Segmented control filtrów](patterns_filter_controls.md) — jak budować segmented controls (status, sort, order) z zachowaniem query params w 500ki i innych modułach
- [Publiczne strony treściowe](patterns_public_standalone_pages.md) — dziedziczą z layouts/landing.html (NIE base.html, NIE standalone); brak typography plugin, oba warianty motywu, pt-32 pod fixed navbar
- [Współdzielony badge przez include](patterns_dependency_badge.md) — {% include %} bez {% with %} działa gdy zmienna pętli nazywa się tak samo we wszystkich miejscach użycia; wymaga eager load relacji na KAŻDYM query
