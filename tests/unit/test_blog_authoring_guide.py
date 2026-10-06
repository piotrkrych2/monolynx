"""Spójność przewodnika blog-authoring.md i skilla /monolynx:blog-post z kodem (MON-265).

Przewodnik `src/monolynx/static/blog-authoring.md` jest publicznym opisem składni bloków, reguł lintu,
bliźniaka `.md` i operacji connectorów. Test czyta rejestry i źródła w locie, więc dodanie bloku do
`TEXT_BLOCKS` / `CONTAINER_BLOCKS`, typu alertu, grafiki, reguły lintu albo pola frontmatteru bez wpisu
w przewodniku wywala test. `blog_lint.py` nie ma rejestru reguł, więc kody czytamy z AST źródeł
(wywołania `LintIssue(<literał>, <LEVEL_*>, ...)` oraz literały w `_container_issues`); ekstraktor
pada głośno, gdy nie znajdzie żadnego kodu albo trafi na wywołanie, którego nie umie odczytać.
"""

import ast
import re
from pathlib import Path

import pytest

from monolynx.services.blog_blocks import _ALERT_TITLES, BLOG_GRAPHICS, CONTAINER_BLOCKS, TEXT_BLOCKS, find_block_issues

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
GUIDE_PATH = ROOT / "src" / "monolynx" / "static" / "blog-authoring.md"
BLOG_LINT_PATH = ROOT / "src" / "monolynx" / "services" / "blog_lint.py"
BLOG_BLOCKS_PATH = ROOT / "src" / "monolynx" / "services" / "blog_blocks.py"
BLOG_AI_PATH = ROOT / "src" / "monolynx" / "blog_ai.py"
SKILL_PATH = ROOT / "plugin" / "skills" / "blog-post" / "SKILL.md"
BLOKI_PATH = ROOT / "plugin" / "skills" / "blog-post" / "references" / "bloki.md"
HELP_PATH = ROOT / "plugin" / "skills" / "help" / "SKILL.md"
LLMS_TXT_PATH = ROOT / "src" / "monolynx" / "static" / "llms.txt"
HOW_TO_USE_PATH = ROOT / "src" / "monolynx" / "static" / "how-to-use-monolynx.md"

LEVELS = {"error", "warning", "info"}
BLOG_TOOLS = ("list_blog_posts", "get_blog_post", "set_blog_post_meta", "lint_blog_post", "publish_blog_post", "unpublish_blog_post")
WIKI_TOOLS = ("create_wiki_page", "update_wiki_page")
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


def _h2_section(text: str, heading: str) -> str:
    """Linie od nagłówka H2 `heading` (prefiks, po którym nie ma litery ani cyfry) do linii przed następnym `## `."""
    lines = text.splitlines()
    opening = re.compile(rf"{re.escape(heading)}(?!\w)")
    start = next((index for index, line in enumerate(lines) if opening.match(line)), None)
    assert start is not None, f"brak nagłówka {heading!r}"
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _fence_blocks(text: str) -> tuple[list[str], int | None]:
    """(treści bloków kodu, numer linii niedomkniętego płotu albo None).

    Płot zamyka linia z co najmniej tyloma backtickami, ilu miał otwierający, więc przykład w płocie z 4 backtickami
    może zawierać płoty z 3.
    """
    blocks: list[str] = []
    current: list[str] = []
    opening = 0
    open_line = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not opening:
            match = re.match(r"(`{3,})", line)
            if match:
                opening, open_line, current = len(match.group(1)), number, []
            continue
        if re.fullmatch(rf"`{{{opening},}}[ \t]*", line):
            blocks.append("\n".join(current))
            opening = 0
        else:
            current.append(line)
    return blocks, (open_line if opening else None)


def _tool_calls(text: str, tool: str = r"\w+") -> list[tuple[str, str]]:
    """(nazwa toola, argumenty) każdego wywołania `mcp__monolynx__<tool>(...)`, także w prozie; nawiasy w cudzysłowach pomijane."""
    calls: list[tuple[str, str]] = []
    for match in re.finditer(rf"mcp__monolynx__({tool})\(", text):
        depth, in_string, index = 1, False, match.end()
        while index < len(text) and depth:
            char = text[index]
            if char == '"':
                in_string = not in_string
            elif not in_string and char == "(":
                depth += 1
            elif not in_string and char == ")":
                depth -= 1
            index += 1
        assert depth == 0, f"niedomknięty nawias wywołania {match.group(1)}"
        calls.append((match.group(1), text[match.end() : index - 1]))
    return calls


PUBLIC_TRUE = re.compile(r"is_public[\"']?\s*[:=]\s*[\"']?true", re.IGNORECASE)
PUBLIC_FALSE = re.compile(r"is_public\s*=\s*false")


def _guide() -> str:
    assert GUIDE_PATH.is_file(), f"brak przewodnika {GUIDE_PATH}"
    return GUIDE_PATH.read_text(encoding="utf-8")


def _catalog() -> str:
    return _h2_section(_guide(), "## Block catalog")


def _level_constants(tree: ast.Module) -> dict[str, str]:
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.startswith("LEVEL_")
    }


def _lint_issue_rules() -> tuple[dict[str, set[str]], list[int], int]:
    """(kod -> poziomy z `LintIssue(...)`, linie wywołań nieczytelnych jako literał, liczba wywołań z `x.rule`).

    Wywołanie z pierwszym argumentem `<coś>.rule` to przepuszczenie reguł bloków graficznych z `find_block_issues`;
    ich kody czyta `_container_rules`.
    """
    tree = ast.parse(BLOG_LINT_PATH.read_text(encoding="utf-8"))
    levels = _level_constants(tree)
    rules: dict[str, set[str]] = {}
    unreadable: list[int] = []
    passthrough = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "LintIssue"):
            continue
        first = node.args[0] if node.args else None
        second = node.args[1] if len(node.args) > 1 else None
        if isinstance(first, ast.Attribute) and first.attr == "rule":
            passthrough += 1
            continue
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            unreadable.append(node.lineno)
            continue
        level = None
        if isinstance(second, ast.Name):
            level = levels.get(second.id)
        elif isinstance(second, ast.Constant) and isinstance(second.value, str):
            level = second.value
        if level not in LEVELS:
            unreadable.append(node.lineno)
            continue
        rules.setdefault(first.value, set()).add(level)
    return rules, unreadable, passthrough


def _container_rules() -> tuple[list[str], list[int]]:
    """(kody z `_container_issues`, linie wywołań `append` nieczytelnych jako krotka z literałem na początku)."""
    tree = ast.parse(BLOG_BLOCKS_PATH.read_text(encoding="utf-8"))
    function = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_container_issues"), None)
    assert function is not None, "brak funkcji _container_issues w blog_blocks.py"
    codes: list[str] = []
    unreadable: list[int] = []
    for node in ast.walk(function):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append"):
            continue
        arg = node.args[0] if node.args else None
        head = arg.elts[0] if isinstance(arg, ast.Tuple) and arg.elts else None
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            codes.append(head.value)
        else:
            unreadable.append(node.lineno)
    return codes, unreadable


def _frontmatter_fields() -> tuple[list[str], int]:
    """(nazwy pól listy `fields` z `render_post_markdown_document`, liczba elementów listy)."""
    tree = ast.parse(BLOG_AI_PATH.read_text(encoding="utf-8"))
    function = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "render_post_markdown_document"), None)
    assert function is not None, "brak funkcji render_post_markdown_document w blog_ai.py"
    for node in ast.walk(function):
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        is_fields = any(isinstance(t, ast.Name) and t.id == "fields" for t in targets)
        if is_fields and isinstance(node.value, ast.List):
            names = [
                element.elts[0].value
                for element in node.value.elts
                if isinstance(element, ast.Tuple)
                and element.elts
                and isinstance(element.elts[0], ast.Constant)
                and isinstance(element.elts[0].value, str)
            ]
            return names, len(node.value.elts)
    raise AssertionError("render_post_markdown_document nie ma listy `fields`")


def _documented_rules(section: str) -> dict[str, str]:
    """Wiersze tabeli `| \\`kod\\` | poziom | ... |` -> {kod: poziom}."""
    rows: dict[str, str] = {}
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        code = re.fullmatch(r"`([a-z0-9-]+)`", cells[0])
        if code and cells[1] in LEVELS:
            rows[code.group(1)] = cells[1]
    return rows


# ---------------------------------------------------------------------------
# Przewodnik: katalog bloków
# ---------------------------------------------------------------------------


class TestBlockCatalog:
    def test_every_registered_block_has_a_heading_in_the_catalog(self):
        names = [*TEXT_BLOCKS, *CONTAINER_BLOCKS]
        assert names, "rejestry bloków są puste - test nic by nie sprawdzał"
        lines = _catalog().splitlines()
        missing = [name for name in names if f"### `{name}`" not in lines]
        assert not missing, f"sekcja '## Block catalog' nie ma nagłówka ### `nazwa` dla bloków: {missing}"

    def test_every_container_block_has_an_opening_line_in_the_catalog(self):
        assert CONTAINER_BLOCKS, "rejestr bloków kontenerowych jest pusty"
        lines = _catalog().splitlines()
        missing = [name for name in CONTAINER_BLOCKS if not any(re.match(rf":::{re.escape(name)}(?!\w)", line) for line in lines)]
        assert not missing, f"sekcja '## Block catalog' nie ma linii otwierającej :::nazwa dla kontenerów: {missing}"

    def test_every_alert_type_is_listed_in_backticks_in_the_catalog(self):
        assert _ALERT_TITLES, "_ALERT_TITLES jest puste"
        catalog = _catalog()
        missing = [kind for kind in _ALERT_TITLES if f"`[!{kind.upper()}]`" not in catalog]
        assert not missing, f"sekcja '## Block catalog' nie wymienia w backtickach typów alertu: {missing}"

    def test_every_registered_graphic_is_listed_in_backticks_in_the_catalog(self):
        assert BLOG_GRAPHICS, "BLOG_GRAPHICS jest puste"
        catalog = _catalog()
        missing = sorted(name for name in BLOG_GRAPHICS if f"`{name}`" not in catalog)
        assert not missing, f"sekcja '## Block catalog' nie wymienia w backtickach grafik: {missing}"

    @pytest.mark.parametrize("heading", ["## Block catalog", "## Lint rules", "## Markdown twin", "## Connector operations"])
    def test_guide_has_each_required_h2_section_exactly_once(self, heading):
        lines = [line.rstrip() for line in _guide().splitlines()]
        assert lines.count(heading) == 1

    def test_next_h2_after_the_catalog_is_lint_rules(self):
        # Linia "## ..." w przykładzie katalogu stałaby się następnym H2 i ucięła sekcję (a z nią bloki.md).
        lines = [line.rstrip() for line in _guide().splitlines()]
        start = lines.index("## Block catalog")
        following = next((line for line in lines[start + 1 :] if line.startswith("## ")), None)
        assert following == "## Lint rules"

    def test_code_fences_in_the_catalog_are_all_closed(self):
        blocks, unclosed = _fence_blocks(_catalog())
        assert blocks, "katalog nie ma żadnego bloku kodu"
        assert unclosed is None, f"płot kodu otwarty w linii {unclosed} sekcji '## Block catalog' nie jest domknięty"


# ---------------------------------------------------------------------------
# Przewodnik: reguły lintu
# ---------------------------------------------------------------------------


class TestLintRules:
    def test_extractor_reads_every_lint_issue_call_as_a_literal(self):
        rules, unreadable, passthrough = _lint_issue_rules()
        assert rules, "ekstraktor nie znalazł żadnej reguły w blog_lint.py"
        assert unreadable == [], f"LintIssue w blog_lint.py (linie {unreadable}) ma kod albo poziom, którego ekstraktor nie czyta jako literał"
        assert passthrough == 1, "blog_lint.py ma inną liczbę wywołań LintIssue(<x>.rule, ...) niż przepuszczenie reguł bloków"
        conflicting = {code: levels for code, levels in rules.items() if len(levels) != 1}
        assert not conflicting, f"ten sam kod z różnymi poziomami: {conflicting}"

    def test_extractor_reads_every_container_rule_as_a_literal(self):
        codes, unreadable = _container_rules()
        assert codes, "ekstraktor nie znalazł żadnej reguły w _container_issues"
        assert unreadable == [], f"_container_issues (linie {unreadable}) dopisuje regułę, której ekstraktor nie czyta jako literał"

    def test_container_rules_are_emitted_at_error_level(self):
        # Poziom reguł z `_container_issues` nie stoi w źródle obok kodu: nadaje go BlockIssue(level="error").
        codes, _ = _container_rules()
        sample = "\n".join(
            [
                ":::graphic",
                ":::",
                ":::chart",
                "brak tabeli",
                ":::",
                ":::diagram",
                "sam opis bez mermaida",
                ":::",
                ":::diagram",
                "```mermaid",
                "flowchart LR",
                "```",
                ":::",
                ":::terminal",
                "tekst bez bloku ogrodzonego",
                ":::",
            ]
        )
        emitted = {issue.rule: issue.level for issue in find_block_issues(sample)}
        assert emitted, "przykład nie wywołał żadnej reguły bloków graficznych"
        assert set(emitted) <= set(codes)
        assert set(emitted.values()) == {"error"}

    def test_every_lint_rule_is_in_the_guide_table_with_the_source_level(self):
        rules, _, _ = _lint_issue_rules()
        source = {code: next(iter(levels)) for code, levels in rules.items()}
        container_codes, _ = _container_rules()
        source.update({code: "error" for code in container_codes})
        assert source
        documented = _documented_rules(_h2_section(_guide(), "## Lint rules"))
        missing = sorted(code for code in source if code not in documented)
        assert not missing, f"sekcja '## Lint rules' nie ma wiersza dla reguł: {missing}"
        wrong_level = {code: (documented[code], level) for code, level in source.items() if documented[code] != level}
        assert not wrong_level, f"poziom w przewodniku różni się od źródła (przewodnik, źródło): {wrong_level}"

    def test_guide_table_has_no_rule_missing_from_the_source(self):
        rules, _, _ = _lint_issue_rules()
        container_codes, _ = _container_rules()
        known = set(rules) | set(container_codes)
        documented = _documented_rules(_h2_section(_guide(), "## Lint rules"))
        assert documented, "sekcja '## Lint rules' nie ma wierszy z regułami"
        stale = sorted(code for code in documented if code not in known)
        assert not stale, f"przewodnik opisuje reguły, których nie ma w kodzie: {stale}"


# ---------------------------------------------------------------------------
# Przewodnik: bliźniak .md i operacje connectorów
# ---------------------------------------------------------------------------


class TestMarkdownTwinAndConnectors:
    def test_every_frontmatter_field_is_listed_in_backticks_in_the_twin_section(self):
        fields, element_count = _frontmatter_fields()
        assert fields, "ekstraktor nie znalazł pól frontmatteru"
        assert len(fields) == element_count, "lista `fields` ma element, którego ekstraktor nie czyta jako (literał, wartość)"
        section = _h2_section(_guide(), "## Markdown twin")
        missing = [field for field in fields if f"`{field}`" not in section]
        assert not missing, f"sekcja '## Markdown twin' nie wymienia w backtickach pól: {missing}"

    def test_connector_operations_section_lists_blog_and_wiki_tools_in_backticks(self):
        section = _h2_section(_guide(), "## Connector operations")
        missing = [tool for tool in (*BLOG_TOOLS, *WIKI_TOOLS) if f"`{tool}`" not in section]
        assert not missing, f"sekcja '## Connector operations' nie wymienia w backtickach: {missing}"

    def test_guide_has_no_em_dash_or_en_dash(self):
        text = _guide()
        offending = [number for number, line in enumerate(text.splitlines(), start=1) if EM_DASH in line or EN_DASH in line]
        assert not offending, f"przewodnik zawiera U+2014 lub U+2013 w liniach: {offending}"


# ---------------------------------------------------------------------------
# bloki.md i skill
# ---------------------------------------------------------------------------


class TestBlokiReference:
    def test_bloki_reference_equals_the_block_catalog_of_the_guide(self):
        assert BLOKI_PATH.is_file(), f"brak {BLOKI_PATH} (generuje go scripts/sync_skills.py)"
        expected = _catalog().rstrip() + "\n"
        assert BLOKI_PATH.read_text(encoding="utf-8") == expected


class TestBlogPostSkill:
    def _skill(self) -> str:
        assert SKILL_PATH.is_file(), f"brak {SKILL_PATH}"
        return SKILL_PATH.read_text(encoding="utf-8")

    def _frontmatter(self) -> str:
        text = self._skill()
        end = text.find("\n---", 4)
        assert text.startswith("---\n") and end != -1, "SKILL.md bez frontmatteru YAML"
        return text[4:end]

    def test_publish_blog_post_appears_only_inside_step_six(self):
        text = self._skill()
        step_six = _h2_section(text, "## KROK 6")
        assert "publish_blog_post" in step_six, "KROK 6 nie wywołuje publikacji"
        assert text.count("publish_blog_post") == step_six.count("publish_blog_post"), "publish_blog_post występuje poza sekcją '## KROK 6'"

    def test_step_six_checks_sprint_run_before_it_publishes(self):
        step_six = _h2_section(self._skill(), "## KROK 6")
        assert "MONOLYNX_SPRINT_RUN" in step_six
        assert step_six.index("MONOLYNX_SPRINT_RUN") < step_six.index("publish_blog_post")

    def test_draft_is_created_private_in_step_three(self):
        text = self._skill()
        creates = _tool_calls(text, "create_wiki_page")
        assert len(creates) == 1, "SKILL.md ma inną liczbę wywołań create_wiki_page niż jedno (zapis szkicu w KROK 3)"
        assert creates == _tool_calls(_h2_section(text, "## KROK 3"), "create_wiki_page"), "create_wiki_page poza sekcją '## KROK 3'"
        assert PUBLIC_FALSE.search(creates[0][1]), "create_wiki_page nie niesie is_public=false"

    def test_no_skill_call_sets_is_public_true_or_publishes_through_page_updates(self):
        text = self._skill()
        calls = _tool_calls(text)
        assert {tool for tool, _ in calls} >= {"create_wiki_page", "update_wiki_page", "publish_blog_post"}
        public = [tool for tool, args in calls if PUBLIC_TRUE.search(args)]
        assert public == [], f"wywołania z is_public=true: {public}"
        updates = _tool_calls(text, "update_wiki_page")
        assert updates, "SKILL.md nie ma wywołania update_wiki_page"
        assert all("is_public" not in args for _, args in updates), "update_wiki_page niesie parametr is_public"

    def test_no_code_block_in_the_skill_sets_is_public_true(self):
        blocks, unclosed = _fence_blocks(self._skill())
        assert blocks
        assert unclosed is None, f"płot kodu otwarty w linii {unclosed} SKILL.md nie jest domknięty"
        public = [block for block in blocks if PUBLIC_TRUE.search(block)]
        assert public == []

    def test_publish_call_does_not_force_past_the_lint_gate(self):
        publishes = _tool_calls(self._skill(), "publish_blog_post")
        assert len(publishes) == 1
        assert "force" not in publishes[0][1]

    def test_skill_points_at_the_block_reference(self):
        assert "references/bloki.md" in self._skill()

    def test_frontmatter_has_a_non_empty_argument_hint(self):
        assert re.search(r"^argument-hint:[ \t]*\S.*$", self._frontmatter(), flags=re.MULTILINE)

    def test_help_line_for_blog_post_carries_the_frontmatter_argument_hint(self):
        hint = re.search(r"^argument-hint:[ \t]*(\S.*?)[ \t]*$", self._frontmatter(), flags=re.MULTILINE)
        assert hint is not None
        lines = [line for line in HELP_PATH.read_text(encoding="utf-8").splitlines() if re.search(r"/monolynx:blog-post(?![\w-])", line)]
        assert len(lines) == 1, f"help/SKILL.md ma {len(lines)} linii z /monolynx:blog-post, oczekiwano 1"
        assert hint.group(1) in lines[0]


class TestGuideIsLinked:
    @pytest.mark.parametrize("path", [LLMS_TXT_PATH, HOW_TO_USE_PATH], ids=lambda p: p.name)
    def test_document_links_to_the_guide(self, path):
        assert "blog-authoring.md" in path.read_text(encoding="utf-8")
