## KROK 3: Ustal etap

Mapa etapow `work` (dla `work-simple` analogicznie: FAZA 0-4):

| Slad | Etap domkniety | Nastepny krok |
|---|---|---|
| brak komentarza planu, brak pipeline | nic - praca nie ruszyla | `/monolynx:work <KEY>` od zera |
| komentarz planu, job `researcher` success, joby agentow `pending` | plan | `work` -> wznow od KROK 6 (spawn zespolu) |
| joby agentow `success`, brak `lint-test` | developerzy | `work` -> wznow od 6e (rekoncyliacja) i 6.5 (lint) |
| `lint-test` success, job krytyka `pending`/`running` | lint | `work` -> wznow od 6.6 (krytyk) |
| review z NEEDS WORK, `attempt` > 1 na jobie agenta, brak nowego review | iteracja w toku | `work` -> wznow od 6.6b (re-run / ponowna ocena) |
| review APPROVED, brak komentarza podsumowania | review | `work` -> wznow od 6.6c (pelny lint) i 6.7 |
| komentarz "Podsumowanie zadania", status nadal `in_progress` | praca skonczona, zamkniecie przerwane (sesja umarla miedzy 7b a 7e) | `work` -> dokoncz 7d-7e: commit wg flag, status `in_review`, domkniecie pipeline; bez re-runu zespolu |
| komentarz "Podsumowanie zadania", status in_review | epizod domkniety | commit/push jesli brudne pliki; po merge `/monolynx:wiki-sync-merge` |
| komentarz "Eskalacja" / "SCOPE GREW" | przerwany | decyzja uzytkownika: `work` (full) albo revert |

Slady sprzeczne (np. pipeline `running` od 2 dni, a komentarz podsumowania
istnieje) -> wypisz obie wersje i wskaz, ktorej ufasz (komentarze sa zapisywane
po pracy, pipeline moze zostac niedomkniety po smierci sesji).
