---
name: rbac-check-order-mutants
description: Review RBAC (MON-241) - mutanty kolejności checków uprawnień, które przeżywają tabelaryczne testy odmowy; jak je szybko wykryć
metadata:
  type: feedback
---

Przy review testów uprawnień sprawdzaj dwa mutanty kolejności, których tabela "rola bez uprawnienia -> odmowa" nie zabija:

1. Dwa checki pod rząd (np. `scrum:write`, potem `scrum:read` w routerze v2): zamiana kolejności przeżywa, jeśli żadna rola w teście nie jest pozbawiona OBU uprawnień naraz. Grepuj w pliku testów rolę bez obu i sprawdź, czy trafia na ten endpoint.
2. Check przesunięty za walidację argumentów albo za lookup: przeżywa, gdy test odmowy podaje poprawne wejście i istniejący zasób. Zabija go dopiero wejście niepoprawne (pusty filename, czas 0, nieistniejące id) z asercją na pełny tekst `Brak uprawnienia <moduł>:<akcja>`.

**Why:** MON-241 - 45 narzędzi w tabeli `CASES`, pełny tekst komunikatu i snapshot bazy zabijały mutanty "brak checku" i "zła akcja", ale kolejność write/read (poprawiana osobną iteracją TM) i check-przed-walidacją w `add_attachment`/`log_time` zostały bez testu.

3. W feedbacku wypisuj KOMPLETNĄ listę miejsc klasy (grep `ensure_permission|require_permission` + co stoi w kolejnych liniach: walidacja, `uuid.UUID(...)`, lookup), nie przykłady. MON-241 runda 2: qa-tester domknął dokładnie nazwane przypadki (`add_attachment`, `log_time`, 9 lookupów), a ta sama klasa przeżyła w ok. 11 innych miejscach (puste `title`/`name`/`filename`, nieistniejący sprint, zły `sprint_id`) - kolejna runda z tym samym -20.

4. Wzorzec, który domknął klasę (MON-241 runda 3, 100/100): osobna tabela `ORDER_CASES` - jeden wiersz na wejście, które za checkiem daje inny błąd, rola pozbawiona dokładnie sprawdzanego uprawnienia, pełny tekst odmowy + snapshot bazy, wariant v2 tylko gdy wejście dociera do handlera (typ `uuid.UUID` w ścieżce i `max_length` w schemacie dają 422 wcześniej). Bramkę flagi (LLM Wiki) zabija tabela odmów przy fladze wyłączonej, walidację za bramką - osobny test z flagą włączoną.

**How to apply:** przy każdym tickecie dodającym checki uprawnień; pełny tekst komunikatu w asercji to mocna strona (odróżnia moduł i akcję), więc szukaj luk tylko w kolejności. Powiązane: [[review_method]].
