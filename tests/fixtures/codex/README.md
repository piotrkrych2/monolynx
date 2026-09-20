# Kontrakt hookow Codex CLI (PreToolUse / UserPromptSubmit)

Zrodlo: https://learn.chatgpt.com/docs/hooks ("Hooks | Codex - OpenAI Developers",
strona `Hooks` w dziale `Codex > Extend and automate`), pobrane `curl`+parsowanie
HTML->tekst dnia **2026-09-20**. Strona zwraca `HTTP 200`, tytul strony (meta
`og:title`): `"Hooks | ChatGPT Learn"`. Nie jest to output CLI (Codex nie byl
zainstalowany w tym srodowisku), wiec fixture opisuje **udokumentowany ksztalt
JSON**, nie przechwycone realne wywolanie procesu. Punkt 1 reguly
`.claude/rules/external-tool-output.md` (fixture z przechwycenia dla parsera
wyjscia narzedzia) tu nie ma zastosowania -- z tego fixture'u nie powstaje
zaden parser wyjscia Codex; sluzy on wylacznie sprawdzeniu, ze istniejacy hook
(`bash_guard.py`) nie wywraca sie na udokumentowanym ksztalcie payloadu.
Cytaty ponizej sa doslowne z pobranej strony.

## PreToolUse - pola wspolne (cytat, sekcja "Common input fields")

> Field Type Meaning session_id string Current Codex session id. **Subagent
> hooks use the parent session id.** transcript_path string | null Path to
> the session transcript file, if any cwd string Working directory for the
> session hook_event_name string Current hook event name model string
> Codex-specific extension. Active model slug

> SessionStart, PreToolUse, PermissionRequest, PostToolUse, UserPromptSubmit,
> SubagentStart, SubagentStop, Stop, and Interrupt also include
> permission_mode, which describes the current permission mode as default,
> acceptEdits, plan, dontAsk, or bypassPermissions.

## PreToolUse - pola specyficzne (cytat, sekcja "PreToolUse")

> Field Type Meaning turn_id string Codex-specific extension. Active Codex
> turn id tool_name string **Canonical hook tool name, such as Bash,
> apply_patch, or an MCP name like mcp__fs__read** tool_use_id string
> Tool-call id for this invocation tool_input JSON value Tool-specific input.
> **Bash and apply_patch use tool_input.command.** MCP and other local
> function tools send their arguments.

Tabela pokrycia narzedzi (cytat, sekcja "Tool coverage"):

> Shell commands Yes Yes Match as Bash. Unified exec (exec_command) Yes Yes
> **Match as Bash.** A later write_stdin poll can deliver the original
> command's PostToolUse when that command finishes.

**Wniosek:** `tool_name == "Bash"` w `bash_guard.py:74` dziala identycznie pod
Codex -- zarowno klasyczna powloka, jak i `exec_command` (unified exec)
raportuja sie jako `"Bash"`. Nazwa narzedzia z briefu Researchera
("exec_command obok Bash") jest myląca: `exec_command` to nazwa *wewnetrznego*
toola Codex, ale hook i tak widzi `tool_name: "Bash"`.

## Znalezisko 1: PreToolUse w Codex NIE ma pol agent_id / agent_type

`agent_id` i `agent_type` wystepuja w dokumentacji WYLACZNIE w sekcjach
`SubagentStart` i `SubagentStop` (cytat):

> SubagentStart ... Field Type Meaning turn_id string ... agent_id string
> Identifier for the subagent agent_type string Subagent type or profile
> permission_mode string Current permission mode

Sekcja `PreToolUse` (pola wypisane wyzej) ich **nie wymienia**. W polu
wspolnym `session_id` dokumentacja wprost mowi: "Subagent hooks use the
parent session id" -- czyli nawet `session_id` nie odroznia subagenta od
sesji glownej wewnatrz PreToolUse.

**Konsekwencja dla `bash_guard.py`:** `is_subagent = bool(agent_id) and
agent_type not in ("", "main")` (linia 82) zawsze wyliczy `False` dla
KAZDEGO wywolania PreToolUse pod Codex, niezaleznie od tego, czy komende
uruchomil subagent czy sesja glowna -- bo Codex nigdy nie wysyla tych pol w
tym evencie. Pod Codex subagent dostanie traktowanie "sesja glowna" (ask
zamiast deny) dla operacji, ktore w Claude Code sa subagentowi twardo
zabronione. To jest luka, nie halucynacja testu: potwierdzona w dokumentacji
producenta, nie da sie jej naprawic samym testem -- wymaga decyzji
architektonicznej (np. inny sygnal odroznienia subagenta pod Codex, jesli
taki istnieje w evencie SubagentStart/Stop, albo swiadome przyjecie luki).
Zgloszone w raporcie koncowym qa-testera do Team Managera, NIE naprawiane w
tym tickecie (poza przydzialem testera).

## Znalezisko 2: `permissionDecision: "ask"` w PreToolUse NIE jest wspierane w Codex

Cytat (sekcja "PreToolUse", pod przykladem `updatedInput`):

> permissionDecision: "ask", legacy decision: "approve", continue: false,
> stopReason, and suppressOutput are parsed but not supported yet. **Codex
> marks the hook run as failed, reports the error, and continues the tool
> call.**

**Konsekwencja:** kazda decyzja `"ask"` zwracana dzis przez `bash_guard.py`
dla sesji glownej (git push bez `MONOLYNX_AUTOPUSH`, `rm -rf` niedestrukcyjny
wobec celu, lint/testy bez `MONOLYNX_AUTOTEST`, merge/approve MR) pod Codex
**nie zablokuje i nie zapyta** -- hook jest oznaczany jako failed, a komenda
i tak sie wykonuje (fail-open). To jest bezposrednio istotne dla AC5/AC6
tego ticketu ("wymagane zgody glownej sesji"): mechanizm "ask" dziala tylko
w Claude Code, nie w Codex. Testy w `test_bash_guard.py` weryfikuja WYLACZNIE
to, co `bash_guard.py` zwraca (zgodnie z kontraktem Claude Code) -- nie da
sie w tym repo przetestowac realnego zachowania Codex (brak CLI), wiec
kazdy test na "ask" jest opisany jako "hook zwraca ask (kontrakt Claude
Code); pod Codex to dzisiaj fails-open, patrz README fixture")." Zgloszone
do raportu koncowego, nie naprawiane tutaj.

## UserPromptSubmit - pola i sposob blokowania (cytat)

> Field Type Meaning turn_id string Codex-specific extension. Active Codex
> turn id prompt string User prompt that's about to be sent

> To block the prompt, return: { "decision": "block", "reason": "..." }
> You can also use exit code 2 and write the blocking reason to stderr.

**Wniosek:** `brief_guard.py` w trybie `block` juz dzis robi dokladnie to
(`sys.exit(2)` + komunikat na stderr) -- zgodne z Codex bez zmian. Tryb
`auto` uzywa `hookSpecificOutput.additionalContext`, co jest udokumentowane
dla `UserPromptSubmit` (cytat): "JSON on stdout supports Common output
fields and this hook-specific shape: { hookSpecificOutput: { hookEventName:
"UserPromptSubmit", additionalContext: "..." } }" -- rowniez zgodne.

## Czego NIE dalo sie zweryfikowac

- Rzeczywiste zachowanie `--plugin` / `.codex/hooks.json` w zainstalowanym
  Codex CLI (brak dostepu do narzedzia w tym srodowisku) -- fixture opisuje
  WYLACZNIE udokumentowany ksztalt JSON, nie zachowanie egzekwowane w
  praktyce.
- Czy istnieje jakikolwiek inny sygnal w PreToolUse pozwalajacy odroznic
  subagenta Codex (np. po `cwd` worktree albo po korelacji z ostatnim
  `SubagentStart` w tej samej sesji) -- dokumentacja tego nie opisuje,
  wiec nie zakladamy takiego mechanizmu w testach.
- Plugin `README.md:269` (twierdzenie Researchera o `CLAUDE_PLUGIN_ROOT`
  jako aliasie zgodnosci i `Bash` jako aliasie `exec_command`) -- czesc o
  `Bash`/`exec_command` POTWIERDZONA (patrz tabela pokrycia narzedzi wyzej,
  z zastrzezeniem ze to jest odwrotna relacja: `exec_command` mapuje sie NA
  `Bash`, nie odwrotnie); czesc o `CLAUDE_PLUGIN_ROOT` jako "aliasie
  zgodnosci" nie zostala odnaleziona na stronie `hooks` i nie jest tu
  potwierdzana ani zaprzeczana -- poza zakresem tego przydzialu (dotyczy
  `plugin/hooks/hooks.json`, ktorego nie edytuje qa-tester).

## Pliki

- `pretooluse_bash.json` - syntetyczny payload PreToolUse zbudowany z pol
  udokumentowanych wyzej (nie przechwycony z realnego CLI, patrz `_meta`).
- `userpromptsubmit.json` - jw. dla UserPromptSubmit.
- `exec-json.0.155.1.jsonl` - **pierwszy plik w tym katalogu, ktory JEST
  realnym przechwyceniem** (w odroznieniu od dwoch powyzszych, ktore sa
  syntetyczne/z dokumentacji, patrz uwagi przy kazdym). Przechwycone przez
  Team Managera 2026-09-20, sandbox `read-only`, katalog poza repo
  (`/tmp/codexprobe`), `codex-cli 0.155.1`, kod wyjscia `0`. Komenda:
  `codex exec --json --sandbox read-only --skip-git-repo-check -C
  /tmp/codexprobe "Reply with exactly the word PONG and nothing else. Do not
  use any tools."`. Plik ma naglowek `_meta` jako pierwsza linia (komenda,
  wersja, data, `captured_by`) - ta linia NIE jest czescia realnego wyjscia
  `codex exec --json`, testy pomijaja ja przed parsowaniem. Pozostale 5 linii
  to doslowne stdout (JSONL: `thread.started`, `turn.started`,
  `item.completed` typu `error` - ostrzezenie o skroconych opisach skilli,
  nie fatalny blad turn - `item.completed` typu `agent_message` z tekstem
  `"PONG"`, `turn.completed`). stderr tego samego przebiegu (ANSI, NIE jest
  JSONL: `rmcp::transport::worker` fatal `Transport channel closed` przy
  `AuthRequired`) jest osobnym strumieniem i CELOWO nie wchodzi do tego
  pliku - parser `parse_codex_jsonl` (MON-185, `plugin/scripts/
  sprint_run_backend.py`) czyta wylacznie stdout. Uzyty w
  `tests/unit/test_sprint_run_backend.py`.
- `exec-plain.0.155.1.stdout.txt` i `exec-plain.0.155.1.stderr.txt` - **drugie realne
  przechwycenie**, tym razem `codex exec` BEZ `--json`, czyli sciezka, ktorej uzywa
  petla `plugin/scripts/sprint_run.sh` (grep po zamrozonej linii statusu). Przechwycone
  przez Team Managera 2026-09-20, `codex-cli 0.155.1`, sandbox `read-only`, katalog poza
  repo (`/tmp/codexprobe2`), stdin z `/dev/null`, kod wyjscia `0`. Komenda:
  `codex exec --sandbox read-only --skip-git-repo-check -C /tmp/codexprobe2 "<prompt
  proszacy o jedna linie SPRINT-RUN>" < /dev/null`. Oba pliki to doslowne strumienie,
  bez naglowka `_meta` (stdout ma byc parsowalny 1:1 tak, jak widzi go skrypt).
  Wnioski utrwalone przez te fixture:
  - **stdout to sama finalna odpowiedz agenta**: jedna linia zakonczona `\n`, ZERO kodow
    ANSI i zero prefiksow. Zamrozona linia `SPRINT-RUN: remaining=... running=...
    waiting=... done=...` przechodzi przez `grep -E` bez zdejmowania ANSI - inaczej niz
    `claude logs`, ktore jest renderem TUI (pkt 4 reguly `external-tool-output.md`).
  - **stderr to render sesji**: naglowek (`workdir`, `model`, `sandbox`, `session id`),
    kody ANSI, linie hookow, echo promptu, szum klienta MCP i `tokens used`. NIE jest
    parsowalny i zaden kod go nie parsuje. Z `2>&1` (jak w skrypcie) linia statusu
    pojawia sie dwa razy i NIE bierze sie to z echa promptu: echo zaczyna sie od tresci
    promptu, wiec kotwica `^` w `grep -E '^SPRINT-RUN: ...'` go nie lapie. Powod jest
    inny - codex renderuje **finalna odpowiedz agenta takze na stderr**, pod znacznikiem
    `codex` (linia 22 tego fixture'u). Dlatego drugie trafienie wystapi rowniez wtedy,
    gdy prompt nie zawiera linii statusu, i dlatego `tail -1` jest konieczny, a nie
    wygodny. Prostuje to docstring testu
    `test_stderr_alone_also_matches_because_codex_renders_final_answer_there_too`
    (znalezisko krytyka, MON-185 ponowne review).
  - **`codex exec` czyta stdin**: nawet przy `< /dev/null` wypisuje `Reading additional
    input from stdin...`. Wywolanie ze stdin-em bedacym otwartym pipe czeka na EOF, wiec
    kazde uruchomienie w tle wymaga jawnego `< /dev/null`.
