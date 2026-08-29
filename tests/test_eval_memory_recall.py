"""Eval tests for the agent memory system (Neo4j backend).

Every test shells out via ``docker exec`` to run Cypher against the live
Neo4j container. When Neo4j is unreachable the whole module skips instead
of failing so these evals only gate environments that actually run the
memory stack.
"""

import re
import subprocess

import pytest

CONTAINER = "neo4j"
USER = "neo4j"
PASSWORD = "neo4j-homelab"

EXPECTED_NODE_TYPES = [
    "Memory",
    "Document",
    "Entity",
    "Session",
    "Topic",
    "Person",
    "Goal",
    "Tool",
    "Company",
    "Location",
]

TOTAL_NODES_MIN = 50
MEMORY_NODES_MIN = 10

REACHABILITY_QUERY = "RETURN 1 AS ok;"


def run_cypher(query):
    """Run one Cypher statement inside the neo4j container."""
    cmd = [
        "docker",
        "exec",
        CONTAINER,
        "cypher-shell",
        "-u",
        USER,
        "-p",
        PASSWORD,
        query,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def query_scalar_int(query):
    """Run a query expected to return a single integer; fail loudly otherwise."""
    result = run_cypher(query)
    assert result.returncode == 0, (
        f"Cypher query failed: {query}\n{result.stderr.strip()}"
    )
    match = re.search(r"-?\d+", result.stdout)
    assert match, f"Query returned no integer value: {query}\n{result.stdout!r}"
    return int(match.group())


@pytest.fixture(scope="module", autouse=True)
def require_neo4j():
    """Skip every test in this module unless Neo4j accepts connections."""
    try:
        result = run_cypher(REACHABILITY_QUERY)
    except (subprocess.TimeoutExpired, OSError) as exc:
        pytest.skip(f"Neo4j unreachable: {exc}")
    if result.returncode != 0:
        pytest.skip(
            "Neo4j unreachable: "
            + (result.stderr.strip() or f"exit code {result.returncode}")
        )


def test_neo4j_running_and_accepting_connections():
    """RETURN 1 round-trips through cypher-shell."""
    result = run_cypher(REACHABILITY_QUERY)
    assert result.returncode == 0, (
        f"Neo4j refused connection: {result.stderr.strip()}"
    )
    assert re.search(r"\b1\b", result.stdout), (
        f"Unexpected response to RETURN 1: {result.stdout!r}"
    )


@pytest.mark.parametrize("label", EXPECTED_NODE_TYPES)
def test_expected_node_type_exists(label):
    """Every expected label is present with at least one node."""
    count = query_scalar_int(f"MATCH (node:{label}) RETURN count(node) AS cnt;")
    assert count > 0, f"Expected at least one :{label} node, found 0"


def test_node_counts_are_reasonable():
    """Graph is populated: >=50 nodes overall, >=10 Memory nodes."""
    total = query_scalar_int("MATCH (node) RETURN count(node) AS cnt;")
    memories = query_scalar_int("MATCH (node:Memory) RETURN count(node) AS cnt;")
    assert total >= TOTAL_NODES_MIN, f"Only {total} nodes, need >= {TOTAL_NODES_MIN}"
    assert memories >= MEMORY_NODES_MIN, (
        f"Only {memories} :Memory nodes, need >= {MEMORY_NODES_MIN}"
    )


def test_relationships_exist_between_nodes():
    """Graph is not just a pile of isolated nodes."""
    rels = query_scalar_int("MATCH ()-[rel]->() RETURN count(rel) AS cnt;")
    connected_nodes = query_scalar_int(
        "MATCH (node)--() RETURN count(DISTINCT node) AS cnt;"
    )
    assert rels > 0, "No relationships found in the graph"
    assert connected_nodes >= 2, (
        "Fewer than 2 nodes participate in relationships; graph is all isolated nodes"
    )


def test_known_hermes_entity_is_findable():
    """Some node mentions 'hermes' somewhere in its properties."""
    count = query_scalar_int(
        "MATCH (node) "
        "WHERE any(key IN keys(node) "
        "          WHERE CASE WHEN node[key] IS :: LIST<ANY> "
        "                     THEN any(item IN node[key] "
        "                              WHERE toLower(toString(item)) CONTAINS 'hermes') "
        "                     ELSE toLower(toString(node[key])) CONTAINS 'hermes' "
        "                END) "
        "RETURN count(node) AS cnt;"
    )
    assert count >= 1, "No node with 'hermes' in its properties was found"


def test_apoc_plugin_loaded_and_functional():
    """APOC is registered and a real function call succeeds."""
    procedures = query_scalar_int(
        "SHOW PROCEDURES YIELD name "
        "WHERE name STARTS WITH 'apoc' "
        "RETURN count(name) AS cnt;"
    )
    assert procedures > 0, "No APOC procedures registered"

    result = run_cypher("RETURN apoc.text.toUpperCase('hermes') AS out;")
    assert result.returncode == 0, f"APOC function call failed: {result.stderr.strip()}"
    assert "HERMES" in result.stdout.upper(), (
        f"APOC call returned unexpected output: {result.stdout!r}"
    )
