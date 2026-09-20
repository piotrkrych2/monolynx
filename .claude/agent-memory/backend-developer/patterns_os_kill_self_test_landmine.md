---
name: patterns-os-kill-self-test-landmine
description: os.kill(pid, SIGTERM) na wlasny proces w niemockowanym tescie zabija runner pytest - jak to wykryc i bezpiecznie zweryfikowac
metadata:
  type: project
---

Gdy implementujesz funkcje typu `stop()`/`kill_session()` ktora wysyla `os.kill(pid, signal.SIGTERM)` do PID zapisanego w rekordzie/rejestrze, a test uzywa `pid=os.getpid()` jako wygodnego "na pewno istniejacego PID" BEZ monkeypatchowania `os.kill` - taki test przy realnym uruchomieniu wysyla SIGTERM do procesu pytest, ktory go wykonuje, i konczy go kodem -15 (bez wyjatku do zlapania, bez szans na dokonczenie asercji). Zweryfikowane empirycznie: `subprocess.run([sys.executable, "-c", "os.kill(os.getpid(), signal.SIGTERM)"])` -> returncode -15, brak stdout po wywolaniu.

**Why:** TDD - testy pisane bez istniejacej implementacji (plik nie istnial w chwili pisania) nie mogly byc naprawde uruchomione, wiec taki landmine przechodzi review "na czytanie" bez wykrycia. Rownowaznie niebezpieczne: `os.kill`/`terminate()`/`Popen.kill()` na `os.getpid()` lub PID pobranym z `os.getpid()` gdziekolwiek w tescie bez mocka.

**How to apply:** przy pisaniu/review implementacji funkcji zabijajacej proces po PID - sprawdz KAZDY test tej funkcji pod katem: czy uzywa `os.getpid()` (albo innego realnie zywego PID) jako danych testowych, i czy `os.kill`/rownowazny syscall jest zmockowany w TYM tescie. Brak mocka + realnie zywy PID = test niebezpieczny do uruchomienia, zglos to jako STOP zamiast czekac az ktos odpali `make test` i straci sesje/worker.

Nie mozna obejsc tego przez dodanie `is_process_alive()`-owego sprawdzenia PRZED SIGTERM wewnatrz `stop()`, jesli `is_process_alive()` rowniez idzie przez `os.kill(pid, 0)` (sygnal zerowy) na TYM SAMYM zamockowanym atrybucie modulu - inny, poprawny test tej samej funkcji moze wymagac strict equality na liscie wywolan `os.kill` (np. `killed == [(pid, SIGTERM)]`), a dodatkowe sondowanie dopisaloby drugi wpis `(pid, 0)` i zepsulo TEN test. Rozwiazaniem jest zaimplementowac funkcje zgodnie ze specyfikacja (bez sondowania) i zglosic niebezpieczne testy do autora testow zamiast przepisywac test lub implementacje w sposob niezgodny ze specyfikacja.

Zwiazane: [[patterns_edit_tool]]
