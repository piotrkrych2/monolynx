---
name: homebrew-update-python-resources
description: Praktyczne gotchas uruchamiania brew update-python-resources / brew style / brew audit lokalnie i w kontenerze homebrew/brew (MON-138)
metadata:
  type: project
---

Zebrane przy generowaniu formuly `cli/packaging/homebrew/monolynx.rb` (MON-138).

**`brew update-python-resources` wymaga formuly wewnatrz prawdziwego tapu.** Bezposrednia sciezka pliku (`brew update-python-resources /sciezka/do/formula.rb`) konczy sie "Homebrew requires formulae to be in a tap". Obejscie: lokalny scratch-tap `brew tap-new local/scratch --no-git` (nic nie instaluje, nic nie sciaga siecia - tylko szkielet katalogu), kopia formuly do `$(brew --repository local/scratch)/Formula/monolynx.rb`, potem `brew update-python-resources local/scratch/monolynx`. Po skonczonej pracy `brew untap local/scratch` (albo docelowa nazwa) - to zmiana stanu Homebrew na hoscie, sprzataj zawsze.

**`--extra-packages` bierze WARTOSC comma-separated, nie liste spacjami oddzielona.** `--extra-packages typer httpx rich` INTERPRETUJE "httpx", "rich" jako kolejne pozycyjne argumenty `formula` (bo usage to `[options] formula [...]`, wiele formul na raz) -> `Error: No available formula with the name "httpx"`. Poprawnie: `--extra-packages=typer,httpx,rich,pydantic`.

**Glowny pakiet formuly musi byc rozwiazywalny przez pip, zeby cokolwiek policzyc.** Jesli main `url`/`sha256` wskazuje na paczke jeszcze nieopublikowana na PyPI (typowe przed pierwszym release), `brew update-python-resources` shelluje do `pip install --dry-run --report=... <main>==<wersja> <extra-packages...>` w JEDNYM wywolaniu i pada z "Unable to determine dependencies for ..." - `--ignore-non-pypi-packages` tego NIE naprawia (dotyczy innej walidacji, nie samego resolvera pip). Obejscie do wygenerowania realnych blokow PRZED pierwszym publish: podmien tymczasowo glowny `url`/`sha256` na jakis PRAWDZIWY, mala paczke bez zaleznosci (np. `platformdirs`, dane z `https://pypi.org/pypi/<pkg>/json`), a docelowa paczke (monolynx-cli) i reszte przenies do `--extra-packages`. Po wygenerowaniu blokow resource wroc do docelowego main url/sha256 (albo, docelowo, uruchom to PO pierwszym `publish-pypi`, kiedy prawdziwy pakiet juz istnieje - wtedy `--extra-packages` moze nie byc juz potrzebne, bo brew czyta `requires_dist` prawdziwych metadanych - NIEZWERYFIKOWANE end-to-end, patrz nizej).

**`python@3.x` w Homebrew jest keg-only** - `brew install python@3.13` NIE dodaje `python3` do PATH automatycznie (symlinki tylko w `$(brew --prefix python@3.13)/libexec/bin/`). Kazdy skrypt CI, ktory wola `python3` po `brew install python@3.13`, musi uzyc pelnej sciezki albo `brew link`.

**Obraz `homebrew/brew:<tag>` (Docker Hub) nie ma `python3` w PATH wcale** (ma `git`, `curl`). Trzeba `brew install python@3.13` (albo cokolwiek formula wymaga) w kontenerze przed uzyciem jakiegokolwiek skryptu Python - dopuszczalne, bo to efemeryczny kontener, nie host. Instalacja zaleznosci python@3.13 (gcc, openssl, ~22 pakiety) trwa kilka minut nawet z bottle'ami.

**Landlock (sandboxing Homebrew) potrafi zablokowac `update-python-resources` w zagniezdzonej VM.** Na Docker Desktop dla macOS (VM w VM), `brew update-python-resources` w kontenerze `homebrew/brew:4.6.20` pada na `Error: Landlock is not supported by this Linux kernel.` po probie shellowania do pip. NIEZWERYFIKOWANE, czy to samo wystapi na "golym" Linuksie realnego GitLab runnera (moze miec jadro z Landlock LSM wlaczonym). Jesli produkcyjny job CI pada na tym samym bledzie, warto sprawdzic `HOMEBREW_NO_SANDBOX` czy podobna flage (NIE sprawdzone w MON-138 - decyzja TM byla "nie kombinuj dalej" po pierwszym trafieniu na blokade).

**Host pip (poza kontenerem Homebrew) moze byc za stary dla `update-python-resources`.** Ta sama komenda na hoscie macOS z `brew install python@3.13` (pip 25.2) pada inaczej: `no such option: --uploaded-prior-to` - flaga, ktorej ta komenda pip uzywa wewnetrznie do resolvowania `pip install --dry-run --report=...`, nie jest jeszcze w stabilnym pip 25.2. POTWIERDZONE ROZWIAZANIE (MON-138, z autoryzacja uzytkownika): `brew upgrade python@3.13` na hoscie podbija pip do 26.2, ktory JUZ zna `--uploaded-prior-to` - wtedy `brew update-python-resources --print-only --extra-packages=...` na formule-brudnopisie (main = prawdziwa mala paczka, docelowa+reszta w --extra-packages) dziala i zwraca realne bloki `resource`. Aktualizacja pip/python na hoscie deweloperskim jest normalnie zakazana - rob to tylko z jawna zgoda uzytkownika.

Zwiazane: [[hatchling_standalone_packages]], [[gitlab_ci_stages_order]].
