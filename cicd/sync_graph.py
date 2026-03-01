#!/usr/bin/env python3
"""Synchronizacja grafu zaleznosci kodu z analizy AST do Neo4j.

Analizuje pliki Python w src/monolynx/ i synchronizuje graf w Neo4j:
- Fully managed (create + delete): nodes, CONTAINS, IMPORTS, INHERITS
- Append-only (create, no delete): CALLS
- Nie zarzadza: USES, IMPLEMENTS (reczne)

Usuniecie node'a kaskadowo usuwa jego krawedzie (DETACH DELETE).

Uzycie:
  docker compose --profile dev exec app python cicd/sync_graph.py
  docker compose --profile dev exec app python cicd/sync_graph.py --dry-run
  docker compose --profile dev exec app python cicd/sync_graph.py --project-id <UUID>
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("sync_graph")

# ---------------------------------------------------------------------------
# Stale
# ---------------------------------------------------------------------------

NODE_TYPES = {"File", "Class", "Method", "Function", "Const", "Module"}

# Typy krawedzi zarzadzane w pelni (create + delete)
MANAGED_EDGE_TYPES = {"CONTAINS", "IMPORTS", "INHERITS"}

# Typy krawedzi append-only (create, nigdy delete)
APPEND_EDGE_TYPES = {"CALLS"}

# Sciezka pliku (wzgledem src/monolynx/) -> prefiks nazwy funkcji
PREFIX_MAP: dict[str, str] = {
    "services/graph.py": "graph",
    "services/embeddings.py": "emb",
    "services/wiki.py": "svc",
    "services/monitoring.py": "mon",
    "services/sprint.py": "svc",
    "services/time_tracking.py": "svc",
    "services/auth.py": "svc",
    "services/email.py": "svc",
    "services/event_processor.py": "svc",
    "services/fingerprint.py": "svc",
    "services/mcp_auth.py": "svc",
    "services/minio_client.py": "minio",
    "services/monitor_loop.py": "mon",
    "services/sidebar.py": "svc",
    "services/ticket_numbering.py": "svc",
    "dashboard/scrum.py": "scrum",
    "dashboard/wiki.py": "wiki",
    "dashboard/monitoring.py": "mon",
    "dashboard/connections.py": "conn",
    "dashboard/auth.py": "dash",
    "dashboard/projects.py": "dash",
    "dashboard/settings.py": "dash",
    "dashboard/users.py": "dash",
    "dashboard/profile.py": "dash",
    "dashboard/sentry.py": "500ki",
    "dashboard/reports.py": "dash",
    "dashboard/helpers.py": "dash",
    "mcp_server.py": "mcp",
    "api/events.py": "api",
    "api/issues.py": "api",
    "cli.py": "cli",
    "worker.py": "cli",
    "config.py": "cfg",
    "constants.py": "const",
    "database.py": "db",
    "main.py": "app",
}

# Katalog -> nazwa modulu
MODULE_MAP: dict[str, str] = {
    "services": "Services",
    "dashboard": "Dashboard",
    "models": "Models",
    "api": "API",
    "schemas": "Schemas",
}

# ---------------------------------------------------------------------------
# Struktury danych
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeKey:
    type: str
    name: str


@dataclass
class NodeDef:
    name: str
    type: str
    file_path: str | None = None
    line_number: int | None = None

    @property
    def key(self) -> NodeKey:
        return NodeKey(self.type, self.name)


@dataclass(frozen=True)
class EdgeKey:
    source_name: str
    target_name: str
    edge_type: str


@dataclass
class EdgeDef:
    source_name: str
    target_name: str
    edge_type: str

    @property
    def key(self) -> EdgeKey:
        return EdgeKey(self.source_name, self.target_name, self.edge_type)


# ---------------------------------------------------------------------------
# Analiza AST
# ---------------------------------------------------------------------------


def _get_prefix(rel_path: str) -> str:
    return PREFIX_MAP.get(rel_path, "")


def _prefixed(prefix: str, name: str) -> str:
    return f"{prefix}:{name}" if prefix else name


def _is_constant(name: str) -> bool:
    return bool(re.match(r"^[A-Z][A-Z0-9_]+$", name))


class ASTAnalyzer:
    """Dwu-przebiegowa analiza AST calego codebase'u."""

    def __init__(self, src_dir: Path) -> None:
        self.src_dir = src_dir
        self.nodes: dict[NodeKey, NodeDef] = {}
        self.edges: dict[EdgeKey, EdgeDef] = {}

        # Mapa: (file_rel_path, bare_name) -> prefixed_name
        # Budowana w pass 1, uzywana w pass 2 dla CALLS
        self.func_registry: dict[tuple[str, str], str] = {}

        # Zbiór znanych nazw klas (do filtrowania CALLS — pomijamy konstruktory)
        self.known_classes: set[str] = set()

    def analyze(self) -> tuple[dict[NodeKey, NodeDef], dict[EdgeKey, EdgeDef]]:
        py_files = sorted(self.src_dir.rglob("*.py"))

        # Pass 1: struktura (nodes, CONTAINS, IMPORTS, INHERITS)
        for py_file in py_files:
            rel_path = str(py_file.relative_to(self.src_dir))
            # Pomin puste pliki (np. __init__.py bez kodu)
            content = py_file.read_text(encoding="utf-8").strip()
            if not content:
                log.debug("Pomijam pusty plik: %s", rel_path)
                continue
            self._pass1_structure(py_file, rel_path)

        # Dodaj Module nodes
        self._add_modules()

        # Pass 2: CALLS
        for py_file in py_files:
            rel_path = str(py_file.relative_to(self.src_dir))
            self._pass2_calls(py_file, rel_path)

        return self.nodes, self.edges

    def _add_node(self, node: NodeDef) -> None:
        key = node.key
        if key in self.nodes:
            existing = self.nodes[key]
            log.debug("Duplikat node: %s (istniejacy: %s)", node, existing)
            return
        self.nodes[key] = node

    def _add_edge(self, edge: EdgeDef) -> None:
        self.edges[edge.key] = edge

    # -- Pass 1 ---------------------------------------------------------------

    def _pass1_structure(self, file_path: Path, rel_path: str) -> None:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError):
            log.warning("Nie mozna sparsowac: %s", rel_path)
            return

        prefix = _get_prefix(rel_path)

        # File node
        self._add_node(NodeDef(
            name=rel_path,
            type="File",
            file_path=f"src/monolynx/{rel_path}",
        ))

        for node in ast.iter_child_nodes(tree):
            # --- Klasy ---
            if isinstance(node, ast.ClassDef):
                self._extract_class(node, rel_path, prefix)

            # --- Funkcje top-level ---
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_name = _prefixed(prefix, node.name)
                self._add_node(NodeDef(
                    name=func_name,
                    type="Function",
                    file_path=f"src/monolynx/{rel_path}",
                    line_number=node.lineno,
                ))
                self._add_edge(EdgeDef(rel_path, func_name, "CONTAINS"))
                self.func_registry[(rel_path, node.name)] = func_name

            # --- Stale ---
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and _is_constant(target.id):
                        self._add_node(NodeDef(
                            name=target.id,
                            type="Const",
                            file_path=f"src/monolynx/{rel_path}",
                            line_number=node.lineno,
                        ))
                        self._add_edge(EdgeDef(rel_path, target.id, "CONTAINS"))

        # --- Importy (monolynx-internal, file->file) ---
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("monolynx."):
                target_rel = self._resolve_import_path(node.module)
                if target_rel and NodeKey("File", target_rel) in self.nodes or target_rel:
                    self._add_edge(EdgeDef(rel_path, target_rel, "IMPORTS"))

    def _extract_class(self, node: ast.ClassDef, rel_path: str, prefix: str) -> None:
        class_name = node.name
        self._add_node(NodeDef(
            name=class_name,
            type="Class",
            file_path=f"src/monolynx/{rel_path}",
            line_number=node.lineno,
        ))
        self._add_edge(EdgeDef(rel_path, class_name, "CONTAINS"))
        self.known_classes.add(class_name)

        # Dziedziczenie
        for base in node.bases:
            base_name = None
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name and base_name not in ("object", "BaseModel", "Base"):
                self._add_edge(EdgeDef(class_name, base_name, "INHERITS"))

        # Metody
        for item in ast.iter_child_nodes(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("__") and item.name.endswith("__"):
                    continue
                method_name = _prefixed(prefix, item.name) if prefix else f"{class_name}.{item.name}"
                self._add_node(NodeDef(
                    name=method_name,
                    type="Method",
                    file_path=f"src/monolynx/{rel_path}",
                    line_number=item.lineno,
                ))
                self._add_edge(EdgeDef(class_name, method_name, "CONTAINS"))
                self.func_registry[(rel_path, item.name)] = method_name

    def _resolve_import_path(self, module: str) -> str | None:
        """Zamien 'monolynx.services.graph' na 'services/graph.py'."""
        parts = module.replace("monolynx.", "", 1).split(".")
        candidate = "/".join(parts) + ".py"
        if (self.src_dir / candidate).exists():
            return candidate
        # Moze to pakiet (katalog z __init__.py)
        candidate_init = "/".join(parts) + "/__init__.py"
        if (self.src_dir / candidate_init).exists():
            return candidate_init
        return None

    def _add_modules(self) -> None:
        """Dodaj node'y Module i edge CONTAINS Module->File."""
        seen_modules: set[str] = set()
        root_files: list[str] = []

        for key in list(self.nodes):
            if key.type != "File":
                continue
            rel_path = key.name
            parts = rel_path.split("/")
            if len(parts) > 1:
                dir_name = parts[0]
                if dir_name in MODULE_MAP:
                    module_name = MODULE_MAP[dir_name]
                    seen_modules.add(module_name)
                    self._add_edge(EdgeDef(module_name, rel_path, "CONTAINS"))
            else:
                root_files.append(rel_path)

        for module_name in seen_modules:
            self._add_node(NodeDef(name=module_name, type="Module"))

        if root_files:
            self._add_node(NodeDef(name="Core", type="Module"))
            for f in root_files:
                self._add_edge(EdgeDef("Core", f, "CONTAINS"))

    # -- Pass 2: CALLS --------------------------------------------------------

    def _pass2_calls(self, file_path: Path, rel_path: str) -> None:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError):
            return

        # Zbuduj mape importow dla tego pliku:
        # direct_imports: local_name -> (target_file, bare_name)
        # module_imports: alias -> target_file
        direct_imports: dict[str, tuple[str, str]] = {}
        module_imports: dict[str, str] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not node.module or not node.module.startswith("monolynx."):
                continue

            target_file = self._resolve_import_path(node.module)
            if not target_file:
                continue

            for alias in node.names:
                imported_name = alias.name
                local_name = alias.asname or imported_name
                # Sprawdz czy to import modulu czy obiektu
                # Jesli imported_name to nazwa pliku/pakietu -> module import
                candidate_file = self._resolve_import_path(node.module + "." + imported_name)
                if candidate_file:
                    module_imports[local_name] = candidate_file
                else:
                    direct_imports[local_name] = (target_file, imported_name)

        # Przeszukaj ciala funkcji/metod w szukaniu wywolan
        prefix = _get_prefix(rel_path)

        for top_node in ast.iter_child_nodes(tree):
            # Funkcje top-level
            if isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                caller = self.func_registry.get((rel_path, top_node.name))
                if caller:
                    self._find_calls_in(top_node, caller, direct_imports, module_imports)

            # Metody w klasach
            elif isinstance(top_node, ast.ClassDef):
                for item in ast.iter_child_nodes(top_node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name.startswith("__") and item.name.endswith("__"):
                            continue
                        caller = self.func_registry.get((rel_path, item.name))
                        if caller:
                            self._find_calls_in(item, caller, direct_imports, module_imports)

    def _find_calls_in(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        caller_name: str,
        direct_imports: dict[str, tuple[str, str]],
        module_imports: dict[str, str],
    ) -> None:
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue

            target_name = self._resolve_call_target(node, direct_imports, module_imports)
            if target_name and target_name != caller_name:
                # Sprawdz czy target istnieje jako Function lub Method
                if (
                    NodeKey("Function", target_name) in self.nodes
                    or NodeKey("Method", target_name) in self.nodes
                ):
                    self._add_edge(EdgeDef(caller_name, target_name, "CALLS"))

    def _resolve_call_target(
        self,
        call_node: ast.Call,
        direct_imports: dict[str, tuple[str, str]],
        module_imports: dict[str, str],
    ) -> str | None:
        func = call_node.func

        # Proste wywolanie: foo()
        if isinstance(func, ast.Name):
            local_name = func.id
            if local_name in direct_imports:
                target_file, bare_name = direct_imports[local_name]
                return self.func_registry.get((target_file, bare_name))
            return None

        # Wywolanie atrybutu: module.foo() lub await service.foo()
        if isinstance(func, ast.Attribute):
            attr_name = func.attr
            value = func.value

            # obj.foo() — sprawdz czy obj to zaimportowany modul
            if isinstance(value, ast.Name):
                obj_name = value.id
                if obj_name in module_imports:
                    target_file = module_imports[obj_name]
                    return self.func_registry.get((target_file, attr_name))
                # Moze to direct import obiektu z atrybutem
                if obj_name in direct_imports:
                    # np. graph_service = import, graph_service.create_node()
                    # Ale to import obiektu, nie modulu — pomijamy
                    pass
            return None

        return None


# ---------------------------------------------------------------------------
# Neo4j sync
# ---------------------------------------------------------------------------


class Neo4jSync:
    """Synchronizacja z Neo4j — odczyt stanu, tworzenie/usuwanie."""

    def __init__(self, uri: str, user: str, password: str, project_id: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.project_id = project_id
        log.info("Polaczono z Neo4j: %s", uri)

    def close(self) -> None:
        self.driver.close()

    def get_current_nodes(self) -> dict[NodeKey, dict[str, Any]]:
        """Pobierz wszystkie node'y projektu z Neo4j."""
        node_types_list = list(NODE_TYPES)
        results: dict[NodeKey, dict[str, Any]] = {}

        with self.driver.session() as session:
            for ntype in node_types_list:
                records = session.run(
                    f"MATCH (n:{ntype} {{project_id: $pid}}) "
                    f"RETURN n.id AS id, n.name AS name, n.file_path AS file_path",
                    pid=self.project_id,
                )
                for rec in records:
                    key = NodeKey(ntype, rec["name"])
                    results[key] = {
                        "id": rec["id"],
                        "name": rec["name"],
                        "type": ntype,
                        "file_path": rec["file_path"],
                    }

        log.info("Odczytano %d node'ow z Neo4j", len(results))
        return results

    def get_current_edges(self) -> dict[EdgeKey, dict[str, Any]]:
        """Pobierz wszystkie krawedzie projektu z Neo4j."""
        results: dict[EdgeKey, dict[str, Any]] = {}

        with self.driver.session() as session:
            records = session.run(
                "MATCH (a {project_id: $pid})-[r]->(b {project_id: $pid}) "
                "RETURN a.name AS source, b.name AS target, type(r) AS etype, "
                "a.id AS source_id, b.id AS target_id",
                pid=self.project_id,
            )
            for rec in records:
                key = EdgeKey(rec["source"], rec["target"], rec["etype"])
                results[key] = {
                    "source_name": rec["source"],
                    "target_name": rec["target"],
                    "edge_type": rec["etype"],
                    "source_id": rec["source_id"],
                    "target_id": rec["target_id"],
                }

        log.info("Odczytano %d krawedzi z Neo4j", len(results))
        return results

    def create_nodes(self, nodes: list[NodeDef]) -> int:
        """Tworz node'y w Neo4j. Zwraca ilosc utworzonych."""
        if not nodes:
            return 0
        created = 0
        with self.driver.session() as session:
            for node in nodes:
                node_id = uuid.uuid4().hex
                session.run(
                    f"CREATE (n:{node.type} {{id: $id, project_id: $pid, name: $name, "
                    f"file_path: $fp, line_number: $ln, metadata: $meta}})",
                    id=node_id,
                    pid=self.project_id,
                    name=node.name,
                    fp=node.file_path,
                    ln=node.line_number,
                    meta=json.dumps({}),
                )
                created += 1
        return created

    def delete_nodes(self, node_ids: list[str]) -> int:
        """Usun node'y (DETACH DELETE — kaskadowo usuwa krawedzie)."""
        if not node_ids:
            return 0
        deleted = 0
        with self.driver.session() as session:
            for nid in node_ids:
                session.run(
                    "MATCH (n {id: $id, project_id: $pid}) DETACH DELETE n",
                    id=nid,
                    pid=self.project_id,
                )
                deleted += 1
        return deleted

    def create_edges(self, edges: list[EdgeDef]) -> int:
        """Tworz krawedzie w Neo4j. Zwraca ilosc utworzonych."""
        if not edges:
            return 0
        created = 0
        with self.driver.session() as session:
            for edge in edges:
                result = session.run(
                    f"MATCH (a {{name: $src, project_id: $pid}}), "
                    f"(b {{name: $tgt, project_id: $pid}}) "
                    f"CREATE (a)-[r:{edge.edge_type} {{metadata: $meta}}]->(b) "
                    f"RETURN count(r) AS cnt",
                    src=edge.source_name,
                    tgt=edge.target_name,
                    pid=self.project_id,
                    meta=json.dumps({}),
                )
                rec = result.single()
                if rec and rec["cnt"] > 0:
                    created += 1
        return created

    def delete_edges(self, edges_data: list[dict[str, Any]]) -> int:
        """Usun krawedzie po source_id, target_id, edge_type."""
        if not edges_data:
            return 0
        deleted = 0
        with self.driver.session() as session:
            for ed in edges_data:
                session.run(
                    f"MATCH (a {{id: $sid}})-[r:{ed['edge_type']}]->(b {{id: $tid}}) DELETE r",
                    sid=ed["source_id"],
                    tid=ed["target_id"],
                )
                deleted += 1
        return deleted

    def auto_detect_project_id(self) -> str | None:
        """Wykryj project_id z istniejacych node'ow (jesli jest dokladnie 1 projekt)."""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (n) WHERE n.project_id IS NOT NULL "
                "RETURN DISTINCT n.project_id AS pid LIMIT 5"
            )
            pids = [rec["pid"] for rec in result]

        if len(pids) == 1:
            return pids[0]
        if len(pids) == 0:
            log.warning("Brak node'ow w grafie — nie mozna wykryc project_id")
        else:
            log.warning("Znaleziono %d projektow w grafie: %s", len(pids), pids)
        return None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def compute_diff(
    desired_nodes: dict[NodeKey, NodeDef],
    current_nodes: dict[NodeKey, dict[str, Any]],
    desired_edges: dict[EdgeKey, EdgeDef],
    current_edges: dict[EdgeKey, dict[str, Any]],
) -> tuple[list[NodeDef], list[str], list[EdgeDef], list[dict[str, Any]]]:
    """Oblicz roznice miedzy stanem desired a current.

    Returns:
        (nodes_to_create, node_ids_to_delete, edges_to_create, edges_to_delete)
    """
    # -- Nodes --
    nodes_to_create = [
        desired_nodes[k] for k in desired_nodes if k not in current_nodes
    ]
    node_ids_to_delete = [
        current_nodes[k]["id"] for k in current_nodes if k not in desired_nodes
    ]

    # -- Edges --
    # Managed edges: full diff (create + delete)
    edges_to_create: list[EdgeDef] = []
    edges_to_delete: list[dict[str, Any]] = []

    for k, edge in desired_edges.items():
        if k not in current_edges:
            if edge.edge_type in MANAGED_EDGE_TYPES:
                edges_to_create.append(edge)
            elif edge.edge_type in APPEND_EDGE_TYPES:
                edges_to_create.append(edge)

    for k, edge_data in current_edges.items():
        if k not in desired_edges:
            # Usuwamy tylko managed edges
            if edge_data["edge_type"] in MANAGED_EDGE_TYPES:
                edges_to_delete.append(edge_data)
            # CALLS/USES: nie usuwamy — sa append-only

    return nodes_to_create, node_ids_to_delete, edges_to_create, edges_to_delete


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synchronizacja grafu kodu (AST -> Neo4j)",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="UUID projektu w grafie. Jesli nie podano, auto-detekcja.",
    )
    parser.add_argument(
        "--src-dir",
        default="src/monolynx",
        help="Sciezka do katalogu zrodlowego (default: src/monolynx)",
    )
    parser.add_argument(
        "--neo4j-uri",
        default=None,
        help="URI Neo4j (default: env NEO4J_URI lub bolt://neo4j:7687)",
    )
    parser.add_argument(
        "--neo4j-user",
        default=None,
        help="Uzytkownik Neo4j (default: env NEO4J_USER lub neo4j)",
    )
    parser.add_argument(
        "--neo4j-password",
        default=None,
        help="Haslo Neo4j (default: env NEO4J_PASSWORD lub neo4j_dev)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tylko pokaz co by sie zmienilo, bez zapisu",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Szczegolowe logi",
    )
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    import os

    neo4j_uri = args.neo4j_uri or os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    neo4j_user = args.neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = args.neo4j_password or os.environ.get("NEO4J_PASSWORD", "neo4j_dev")

    src_dir = Path(args.src_dir)
    if not src_dir.exists():
        log.error("Katalog zrodlowy nie istnieje: %s", src_dir)
        sys.exit(1)

    # 1. Analiza AST
    log.info("=== Pass 1+2: Analiza AST (%s) ===", src_dir)
    analyzer = ASTAnalyzer(src_dir)
    desired_nodes, desired_edges = analyzer.analyze()
    log.info(
        "AST: %d node'ow, %d krawedzi",
        len(desired_nodes),
        len(desired_edges),
    )

    # Podsumowanie per typ
    node_counts: dict[str, int] = {}
    for k in desired_nodes:
        node_counts[k.type] = node_counts.get(k.type, 0) + 1
    edge_counts: dict[str, int] = {}
    for k in desired_edges:
        edge_counts[k.edge_type] = edge_counts.get(k.edge_type, 0) + 1
    log.info("  Nodes: %s", dict(sorted(node_counts.items())))
    log.info("  Edges: %s", dict(sorted(edge_counts.items())))

    # 2. Polaczenie z Neo4j i odczyt stanu
    log.info("=== Odczyt stanu z Neo4j ===")
    sync = Neo4jSync(neo4j_uri, neo4j_user, neo4j_password, "")

    project_id = args.project_id
    if not project_id:
        project_id = sync.auto_detect_project_id()
        if not project_id:
            log.error("Nie udalo sie wykryc project_id. Podaj --project-id.")
            sync.close()
            sys.exit(1)
        log.info("Auto-detected project_id: %s", project_id)

    sync.project_id = project_id
    current_nodes = sync.get_current_nodes()
    current_edges = sync.get_current_edges()

    # 3. Oblicz diff
    log.info("=== Obliczanie diff ===")
    nodes_to_create, node_ids_to_delete, edges_to_create, edges_to_delete = compute_diff(
        desired_nodes, current_nodes, desired_edges, current_edges,
    )

    log.info("Diff:")
    log.info("  Nodes: +%d, -%d", len(nodes_to_create), len(node_ids_to_delete))
    log.info("  Edges: +%d, -%d", len(edges_to_create), len(edges_to_delete))

    if args.verbose:
        for n in nodes_to_create:
            log.debug("  + Node [%s] %s", n.type, n.name)
        for nid in node_ids_to_delete:
            # Znajdz nazwe
            name = next(
                (v["name"] for v in current_nodes.values() if v["id"] == nid),
                nid,
            )
            log.debug("  - Node %s", name)
        for e in edges_to_create:
            log.debug("  + Edge %s -[%s]-> %s", e.source_name, e.edge_type, e.target_name)
        for e in edges_to_delete:
            log.debug(
                "  - Edge %s -[%s]-> %s",
                e["source_name"],
                e["edge_type"],
                e["target_name"],
            )

    if not nodes_to_create and not node_ids_to_delete and not edges_to_create and not edges_to_delete:
        log.info("Graf jest aktualny — brak zmian.")
        sync.close()
        return

    # 4. Zastosuj zmiany
    if args.dry_run:
        log.info("=== DRY RUN — zmiany NIE zostaly zastosowane ===")
        sync.close()
        return

    log.info("=== Stosowanie zmian ===")

    # Najpierw usun node'y (kaskadowo usunie ich krawedzie)
    deleted_nodes = sync.delete_nodes(node_ids_to_delete)
    log.info("Usunieto %d node'ow", deleted_nodes)

    # Potem usun krawedzie (managed, bez nodes cascade)
    deleted_edges = sync.delete_edges(edges_to_delete)
    log.info("Usunieto %d krawedzi", deleted_edges)

    # Tworz nowe node'y
    created_nodes = sync.create_nodes(nodes_to_create)
    log.info("Utworzono %d node'ow", created_nodes)

    # Tworz nowe krawedzie
    created_edges = sync.create_edges(edges_to_create)
    log.info("Utworzono %d krawedzi", created_edges)

    log.info("=== Synchronizacja zakonczona ===")
    sync.close()


if __name__ == "__main__":
    main()
