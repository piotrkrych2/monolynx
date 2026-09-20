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
