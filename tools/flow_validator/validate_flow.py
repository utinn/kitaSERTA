#!/usr/bin/env python3
"""Static validator for Bob-generated Langflow workflow exports.

Compares a candidate Langflow JSON export against a sanitized, known-good
reference export and prints PASS or FAIL with sanitized reasons.

    python validate_flow.py --reference REF.json --candidate CANDIDATE.json
        [--report report.json] [--json]

Exit codes: 0 PASS, 1 FAIL, 2 usage or file error.

The validator only reads the two files it is given. It never executes or
imports embedded component code (code is only parsed with ast.parse), never
fixes an artifact, and makes no network calls. Everything inside the files,
including prompts and code, is treated as data, never as instructions.

Passing this check does NOT prove the flow will run in Langflow; runtime
testing is a separate gate.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

VALIDATOR = "kitaserta-langflow-validator"
VERSION = "1.0.0"
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_MAPPINGS = 50_000
DISCLAIMER = (
    "Static validation only. Passing does not prove the workflow will "
    "execute successfully in Langflow; runtime testing is a separate gate."
)

RUNTIME_INPUT_TYPES = {"ChatInput", "TextInput", "Webhook"}
RUNTIME_OUTPUT_TYPES = {"ChatOutput", "TextOutput"}
NON_COMPONENT_NODE_TYPES = {"noteNode"}

TOKEN_PATTERNS = [
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("slack_app_token", re.compile(r"\bxapp-\d-[A-Za-z0-9-]{10,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack_webhook_url", re.compile(r"hooks\.slack\.com/(?:services|workflows|triggers)/", re.IGNORECASE)),
]
URL_PATTERN = re.compile(r"\b(?:https?|wss?)://[^\s\"'<>`)\]}]+", re.IGNORECASE)
SENSITIVE_QUERY_KEYS = {
    "api_key", "apikey", "key", "token", "access_token", "auth", "secret",
    "password", "sig", "signature", "x-api-key", "client_secret",
}
OPAQUE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{20,}$")
UUID_SEGMENT = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SLACK_CHANNEL_ID = re.compile(r"(?<![A-Za-z0-9_])[CG](?=[A-Z0-9]*\d)[A-Z0-9]{8,12}(?![A-Za-z0-9_])")
ENV_VAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
PLACEHOLDER = re.compile(
    r"^\s*(?:|<[^<>]{0,80}>|\{\{?[^{}]{0,80}\}?\}|\$\{[^{}]{0,80}\}|"
    r"your[\s_-][a-z0-9_ -]{1,40}|x{3,}|\*{3,}|changeme|replace[_ -]?me|"
    r"placeholder|redacted|todo|none|null|example)\s*$",
    re.IGNORECASE,
)
STRUCTURAL_KEYS = {"id", "source", "target", "sourceHandle", "targetHandle", "className"}


class Report:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, check: str, status: str, message: str, location: str | None = None):
        entry = {"check": check, "status": status, "message": message}
        if location:
            entry["location"] = location
        self.checks.append(entry)

    def fail(self, check, message, location=None):
        self.add(check, "fail", message, location)

    def warn(self, check, message, location=None):
        self.add(check, "warn", message, location)

    def ok(self, check, message, location=None):
        self.add(check, "pass", message, location)

    @property
    def failed(self) -> bool:
        return any(c["status"] == "fail" for c in self.checks)


def safe_id(value) -> str:
    """Identifiers come from the artifact: keep them short and printable."""
    text = str(value)
    cleaned = re.sub(r"[^A-Za-z0-9:@._-]", "?", text)[:80]
    return cleaned + ("…" if len(text) > 80 else "")


class ArtifactError(Exception):
    pass


def load_json(path: Path, label: str):
    try:
        size = path.stat().st_size
    except OSError:
        raise ArtifactError(f"{label} file could not be read")
    if size > MAX_FILE_BYTES:
        raise ArtifactError(f"{label} file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        raw = path.read_bytes()
    except OSError:
        raise ArtifactError(f"{label} file could not be read")
    try:
        return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()
    except UnicodeDecodeError:
        raise ArtifactError(f"{label} is not UTF-8 text")
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"{label} is not valid JSON (line {exc.lineno}, column {exc.colno})")
    except RecursionError:
        raise ArtifactError(f"{label} JSON is nested too deeply")


class Graph:
    """Parsed Langflow export: {data: {nodes, edges}} or a bare {nodes, edges}."""

    def __init__(self, doc, label: str, report: Report):
        self.label = label
        self.ok = False
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

        container = doc.get("data") if isinstance(doc, dict) and isinstance(doc.get("data"), dict) else doc
        if not isinstance(container, dict) or not isinstance(container.get("nodes"), list) \
                or not isinstance(container.get("edges"), list):
            report.fail("structure", f"{label}: unsupported format; expected a Langflow export with data.nodes and data.edges lists")
            return

        problems = 0
        for index, raw in enumerate(container["nodes"]):
            node = self.parse_node(raw, index, report)
            if node is None:
                problems += 1
                continue
            if node["id"] in self.nodes:
                report.fail("structure", f"{label}: duplicate node id", f"node {safe_id(node['id'])}")
                problems += 1
                continue
            self.nodes[node["id"]] = node

        for index, raw in enumerate(container["edges"]):
            edge = self.parse_edge(raw, index, report)
            if edge is None:
                problems += 1
            else:
                self.edges.append(edge)

        if not self.components():
            report.fail("structure", f"{label}: the flow has no components")
            problems += 1
        self.ok = problems == 0

    def parse_node(self, raw, index, report):
        where = f"{self.label} node #{index}"
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            report.fail("structure", "node without a string id", where)
            return None
        if raw.get("type") in NON_COMPONENT_NODE_TYPES:
            return {"id": raw["id"], "kind": "note", "type": raw.get("type"), "raw": raw}
        data = raw.get("data")
        node = data.get("node") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("type"), str) or not isinstance(node, dict):
            report.fail("structure", "node is missing data.type or data.node", f"{self.label} node {safe_id(raw['id'])}")
            return None
        template = node.get("template")
        if not isinstance(template, dict):
            report.fail("structure", "node is missing data.node.template", f"{self.label} node {safe_id(raw['id'])}")
            return None
        outputs = {}
        for output in node.get("outputs") or []:
            if isinstance(output, dict) and isinstance(output.get("name"), str):
                types = output.get("types")
                outputs[output["name"]] = set(types) if isinstance(types, list) else set()
        return {
            "id": raw["id"],
            "kind": "component",
            "type": data["type"],
            "display_name": node.get("display_name"),
            "template": template,
            "outputs": outputs,
            "raw": raw,
        }

    def parse_edge(self, raw, index, report):
        where = f"{self.label} edge #{index}"
        if not isinstance(raw, dict) or not isinstance(raw.get("source"), str) \
                or not isinstance(raw.get("target"), str):
            report.fail("edges", "edge without string source/target", where)
            return None
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        source_handle = data.get("sourceHandle")
        target_handle = data.get("targetHandle")
        if not isinstance(source_handle, dict) or not isinstance(target_handle, dict):
            report.fail("edges", "edge is missing connection handles (data.sourceHandle / data.targetHandle)", where)
            return None
        for key, handle in (("sourceHandle", source_handle), ("targetHandle", target_handle)):
            encoded = raw.get(key)
            if encoded is None:
                continue
            try:
                decoded = json.loads(encoded.replace("œ", '"')) if isinstance(encoded, str) else None
            except ValueError:
                decoded = None
            if decoded != handle:
                report.fail("edges", f"edge {key} string does not match its data handle", where)
                return None
        return {
            "source": raw["source"],
            "target": raw["target"],
            "source_handle": source_handle,
            "target_handle": target_handle,
            "output": source_handle.get("name"),
            "field": target_handle.get("fieldName"),
            "where": f"{self.label} edge {safe_id(raw['source'])} -> {safe_id(raw['target'])}",
        }

    def components(self):
        return {nid: n for nid, n in self.nodes.items() if n["kind"] == "component"}


def check_edges(graph: Graph, report: Report) -> None:
    nodes = graph.components()
    connected = set()
    seen = set()
    for edge in graph.edges:
        source, target = nodes.get(edge["source"]), nodes.get(edge["target"])
        if source is None or target is None:
            report.fail("edges", "dangling edge: source or target node does not exist", edge["where"])
            continue
        connected.update({edge["source"], edge["target"]})
        sh, th = edge["source_handle"], edge["target_handle"]
        if sh.get("id") != edge["source"] or th.get("id") != edge["target"]:
            report.fail("edges", "connection handle refers to a different node than the edge", edge["where"])
        if sh.get("dataType") != source["type"]:
            report.fail("edges", "source handle component type does not match the source node", edge["where"])
        if edge["output"] not in source["outputs"]:
            report.fail("edges", f"source output '{safe_id(edge['output'])}' does not exist on the source node", edge["where"])
            continue
        if not isinstance(edge["field"], str) or edge["field"] not in target["template"]:
            report.fail("edges", f"target input '{safe_id(edge['field'])}' does not exist on the target node", edge["where"])
            continue
        output_types = source["outputs"][edge["output"]]
        handle_types = set(sh.get("output_types") or [])
        input_types = set(th.get("inputTypes") or [])
        if not handle_types or not handle_types <= output_types:
            report.fail("edges", "source handle types do not match the source output", edge["where"])
        elif not handle_types & input_types:
            report.fail("edges", "incompatible connection: output types are not accepted by the target input", edge["where"])
        key = (edge["source"], edge["output"], edge["target"], edge["field"])
        if key in seen:
            report.fail("edges", "duplicate connection", edge["where"])
        seen.add(key)

    for node_id in nodes:
        if node_id not in connected:
            report.fail("edges", f"{graph.label}: disconnected node", f"node {safe_id(node_id)}")


def edge_keys(graph: Graph) -> set[tuple]:
    return {(e["source"], e["output"], e["target"], e["field"]) for e in graph.edges}


def map_nodes(ref: Graph, cand: Graph, report: Report) -> dict | None:
    """Match reference nodes to candidate nodes of the same component type,
    choosing the assignment that preserves the most reference connections."""
    ref_groups: dict[str, list[str]] = {}
    cand_groups: dict[str, list[str]] = {}
    for nid, node in ref.components().items():
        ref_groups.setdefault(node["type"], []).append(nid)
    for nid, node in cand.components().items():
        cand_groups.setdefault(node["type"], []).append(nid)

    ok = True
    for ctype, ids in ref_groups.items():
        have = len(cand_groups.get(ctype, []))
        if have < len(ids):
            report.fail("required_nodes", f"missing required component: {safe_id(ctype)} (expected {len(ids)}, found {have})")
            ok = False
    for ctype, ids in cand_groups.items():
        extra = len(ids) - len(ref_groups.get(ctype, []))
        if extra > 0:
            report.fail("required_nodes", f"unexpected component not in the reference: {safe_id(ctype)} (+{extra})")
    if not ok:
        return None

    choices = []
    total = 1
    for ctype, ids in ref_groups.items():
        options = list(itertools.permutations(cand_groups[ctype], len(ids)))
        total *= len(options)
        if total > MAX_MAPPINGS:
            report.fail("required_nodes", "graph is too ambiguous to match against the reference (too many same-type components)")
            return None
        choices.append([dict(zip(ids, option)) for option in options])

    ref_edges = edge_keys(ref)
    cand_edges = edge_keys(cand)
    best, best_score = None, None
    for combo in itertools.product(*choices):
        mapping = {k: v for part in combo for k, v in part.items()}
        preserved = sum((mapping[s], o, mapping[t], f) in cand_edges for s, o, t, f in ref_edges)
        names = sum(ref.nodes[r]["display_name"] == cand.nodes[c]["display_name"] for r, c in mapping.items())
        score = (preserved, names)
        if best_score is None or score > best_score:
            best, best_score = mapping, score
    return best


def check_connections(ref: Graph, cand: Graph, mapping: dict, report: Report) -> None:
    cand_edges = edge_keys(cand)
    expected = set()
    for s, o, t, f in edge_keys(ref):
        key = (mapping[s], o, mapping[t], f)
        expected.add(key)
        if key not in cand_edges:
            report.fail(
                "required_connections",
                f"missing required connection {safe_id(ref.nodes[s]['type'])}.{safe_id(o)} -> "
                f"{safe_id(ref.nodes[t]['type'])}.{safe_id(f)}",
            )
    for key in cand_edges - expected:
        s, o, t, f = key
        if s in cand.nodes and t in cand.nodes:
            report.fail(
                "required_connections",
                f"unexpected connection not in the reference: {safe_id(cand.nodes[s]['type'])}.{safe_id(o)} -> "
                f"{safe_id(cand.nodes[t]['type'])}.{safe_id(f)}",
            )


def check_interfaces(ref: Graph, cand: Graph, mapping: dict, report: Report) -> None:
    """Outputs and input fields of every reference component must be kept
    with the same types."""
    for rid, cid in mapping.items():
        r, c = ref.nodes[rid], cand.nodes[cid]
        where = f"node {safe_id(cid)} ({safe_id(r['type'])})"
        for name, types in r["outputs"].items():
            if name not in c["outputs"]:
                report.fail("interfaces", f"required output '{safe_id(name)}' is missing", where)
            elif c["outputs"][name] != types:
                report.fail("interfaces", f"output '{safe_id(name)}' changed type", where)
        for field, spec in r["template"].items():
            if not isinstance(spec, dict) or field.startswith("_"):
                continue
            other = c["template"].get(field)
            if not isinstance(other, dict):
                report.fail("interfaces", f"required input field '{safe_id(field)}' is missing", where)
                continue
            for attr in ("type", "_input_type"):
                if spec.get(attr) != other.get(attr):
                    report.fail("interfaces", f"input field '{safe_id(field)}' changed {attr}", where)


def check_runtime_io(ref: Graph, cand: Graph, mapping: dict | None, report: Report) -> None:
    ref_nodes = ref.components()
    inputs = [n for n in ref_nodes if ref_nodes[n]["type"] in RUNTIME_INPUT_TYPES]
    outputs = [n for n in ref_nodes if ref_nodes[n]["type"] in RUNTIME_OUTPUT_TYPES]
    if not inputs or not outputs:
        report.fail(
            "runtime_io",
            "reference has no runtime input (Chat/Text Input, Webhook) or no runtime output "
            "(Chat/Text Output) node; unsupported reference",
        )
        return
    if mapping is None:
        return
    targets = {e["target"] for e in cand.edges}
    sources = {e["source"] for e in cand.edges}
    for rid in inputs:
        cid = mapping[rid]
        if cid not in sources:
            report.fail("runtime_io", "runtime input node does not feed the flow", f"node {safe_id(cid)}")
    for rid in outputs:
        cid = mapping[rid]
        if cid not in targets:
            report.fail("runtime_io", "runtime output node receives no result", f"node {safe_id(cid)}")
    if not any(c["status"] == "fail" and c["check"] == "runtime_io" for c in report.checks):
        report.ok("runtime_io", f"runtime input ({len(inputs)}) and output ({len(outputs)}) nodes preserved")


def code_fields(node: dict) -> dict[str, object]:
    return {
        name: spec.get("value")
        for name, spec in node["template"].items()
        if isinstance(spec, dict) and spec.get("type") == "code"
    }


def normalize_code(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def code_hash(text: str) -> str:
    return hashlib.sha256(normalize_code(text).encode("utf-8")).hexdigest()


def parse_error(text: str) -> str | None:
    """ast.parse only: the code is never compiled to bytecode or run."""
    try:
        ast.parse(text, mode="exec")
    except SyntaxError as exc:
        return f"invalid Python syntax (line {exc.lineno})"
    except (ValueError, RecursionError, MemoryError):
        return "Python code could not be parsed"
    return None


def check_code(ref: Graph, cand: Graph, mapping: dict, report: Report) -> list[dict]:
    hashes = []
    for rid, cid in mapping.items():
        r, c = ref.nodes[rid], cand.nodes[cid]
        ref_code, cand_code = code_fields(r), code_fields(c)
        for field, ref_value in ref_code.items():
            where = f"node {safe_id(cid)} ({safe_id(r['type'])}) field {safe_id(field)}"
            if not isinstance(ref_value, str) or not ref_value.strip():
                report.fail("code", "reference component code is empty; unsupported reference", where)
                continue
            error = parse_error(ref_value)
            if error:
                report.fail("code", f"reference component code has {error}; unsupported reference", where)
                continue
            value = cand_code.get(field)
            if not isinstance(value, str) or not value.strip():
                report.fail("code", "component code is missing", where)
                continue
            error = parse_error(value)
            if error:
                report.fail("code", f"component code has {error}", where)
            expected, actual = code_hash(ref_value), code_hash(value)
            entry = {
                "node": safe_id(cid),
                "component": safe_id(r["type"]),
                "field": safe_id(field),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "match": expected == actual,
            }
            hashes.append(entry)
            if expected == actual:
                continue
            ref_norm, cand_norm = normalize_code(ref_value), normalize_code(value)
            ref_lines, cand_lines = ref_norm.count("\n") + 1, cand_norm.count("\n") + 1
            if len(cand_norm) < len(ref_norm) and (ref_norm.startswith(cand_norm[:-1]) or len(cand_norm) < 0.9 * len(ref_norm)):
                report.fail("code", f"protected component code appears truncated ({cand_lines} of {ref_lines} lines)", where)
            else:
                report.fail("code", "protected component code differs from the known-good reference", where)
        for field in cand_code.keys() - ref_code.keys():
            report.fail("code", "unexpected code field not present in the reference",
                        f"node {safe_id(cid)} field {safe_id(field)}")
    return hashes


def is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER.match(value))


def walk_strings(obj, path=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from walk_strings(value, path + (str(key),))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_strings(value, path + (str(index),))
    elif isinstance(obj, str):
        yield path, obj


def url_findings(text: str):
    for match in URL_PATTERN.finditer(text):
        try:
            parts = urlsplit(match.group(0).rstrip(".,;:"))
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if parts.username or parts.password:
            yield "url_with_credentials", host
            continue
        query_keys = {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
        if query_keys & SENSITIVE_QUERY_KEYS:
            yield "url_with_secret_parameter", host
            continue
        segments = [s for s in parts.path.split("/") if s]
        is_docs = host.startswith("docs.") or "readthedocs" in host or (segments[:1] == ["docs"])
        mentions_mcp = "mcp" in host or any(s.lower() in {"mcp", "sse"} for s in segments)
        opaque = any(OPAQUE_SEGMENT.match(s) or UUID_SEGMENT.match(s) for s in segments)
        if mentions_mcp and not is_docs and (opaque or parts.query):
            yield "private_mcp_url", host


def location_of(path: tuple, graph_doc) -> str:
    """Human-readable location without any values: node id + field name."""
    text = ".".join(path)
    node_match = re.search(r"nodes\.(\d+)", text)
    container = graph_doc.get("data") if isinstance(graph_doc.get("data"), dict) else graph_doc
    if node_match:
        try:
            node = container["nodes"][int(node_match.group(1))]
            node_id = safe_id(node.get("id"))
        except (KeyError, IndexError, TypeError, AttributeError):
            node_id = "?"
        field = re.search(r"template\.([^.]+)", text)
        return f"node {node_id}" + (f" field {safe_id(field.group(1))}" if field else f" ({safe_id(path[-1])})")
    return f"top-level {safe_id(text)}"


def scan_secrets(doc, label: str, report: Report) -> None:
    findings = set()
    container = doc.get("data") if isinstance(doc.get("data"), dict) else doc

    for index, node in enumerate(container.get("nodes") or []):
        template = (((node or {}).get("data") or {}).get("node") or {}).get("template") if isinstance(node, dict) else None
        if not isinstance(template, dict):
            continue
        for field, spec in template.items():
            if not isinstance(spec, dict):
                continue
            secret_field = spec.get("_input_type") == "SecretStrInput" or spec.get("password") is True
            value = spec.get("value")
            if not secret_field or not isinstance(value, str) or is_placeholder(value):
                continue
            if spec.get("load_from_db") is True and ENV_VAR_NAME.match(value):
                continue
            findings.add(("embedded_credential", location_of(("data", "nodes", str(index), "data", "node", "template", field), doc)))

    for path, value in walk_strings(doc):
        if path and path[-1] in STRUCTURAL_KEYS:
            continue
        is_code = "template" in path and len(path) >= 2 and path[-1] == "value" and _is_code_path(doc, path)
        for kind, pattern in TOKEN_PATTERNS:
            if pattern.search(value):
                findings.add((kind, location_of(path, doc)))
        for kind, host in url_findings(value):
            findings.add((kind, location_of(path, doc) + (f" host {safe_id(host)}" if host else "")))
        if not is_code and SLACK_CHANNEL_ID.search(value):
            findings.add(("hardcoded_slack_channel_id", location_of(path, doc)))

    messages = {
        "embedded_credential": "credential value stored in a secret input (use a Langflow global variable)",
        "slack_token": "Slack token",
        "slack_app_token": "Slack app token",
        "bearer_token": "bearer token",
        "openai_style_key": "API key",
        "github_token": "GitHub token",
        "aws_access_key": "AWS access key",
        "google_api_key": "Google API key",
        "jwt": "JSON Web Token",
        "private_key": "private key",
        "slack_webhook_url": "Slack webhook URL (hardcoded destination)",
        "url_with_credentials": "URL with embedded credentials",
        "url_with_secret_parameter": "URL with a secret query parameter",
        "private_mcp_url": "private MCP endpoint URL",
        "hardcoded_slack_channel_id": "hardcoded Slack channel ID (destination must come from the approved setup)",
    }
    for kind, where in sorted(findings):
        report.fail("secrets", f"{label}: {messages[kind]} detected (value not shown)", where)
    if not findings:
        report.ok("secrets", f"{label}: no embedded credentials, private MCP URLs or hardcoded destinations found")


def _is_code_path(doc, path) -> bool:
    node = doc
    try:
        for key in path[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
    except (KeyError, IndexError, ValueError, TypeError):
        return False
    return isinstance(node, dict) and node.get("type") == "code"


def validate(reference_path: Path, candidate_path: Path) -> dict:
    report = Report()
    files = {}
    docs = {}
    for label, path in (("reference", reference_path), ("candidate", candidate_path)):
        try:
            docs[label], digest = load_json(path, label)
            files[label] = {"name": path.name, "sha256": digest}
            report.ok("json", f"{label} is valid JSON")
        except ArtifactError as exc:
            files[label] = {"name": path.name, "sha256": None}
            report.fail("json", str(exc))

    hashes: list[dict] = []
    mapping = None
    if len(docs) == 2:
        for label, doc in docs.items():
            if isinstance(doc, dict):
                scan_secrets(doc, label, report)
        ref = Graph(docs["reference"], "reference", report)
        cand = Graph(docs["candidate"], "candidate", report)
        ref_valid = ref.ok
        if ref.ok:
            before = len(report.checks)
            check_edges(ref, report)
            ref_valid = not any(c["status"] == "fail" for c in report.checks[before:])
        if cand.ok:
            check_edges(cand, report)
        if ref_valid and cand.ok:
            mapping = map_nodes(ref, cand, report)
            if mapping is not None:
                report.ok("required_nodes", f"all {len(mapping)} reference components found")
                check_connections(ref, cand, mapping, report)
                check_interfaces(ref, cand, mapping, report)
                hashes = check_code(ref, cand, mapping, report)
            check_runtime_io(ref, cand, mapping, report)
        elif ref.ok and not ref_valid:
            report.fail("structure", "reference graph is invalid; cannot validate against it")

    failures = [c for c in report.checks if c["status"] == "fail"]
    return {
        "validator": VALIDATOR,
        "version": VERSION,
        "result": "FAIL" if failures or len(docs) < 2 or mapping is None else "PASS",
        "files": files,
        "summary": {
            "failures": len(failures),
            "warnings": sum(c["status"] == "warn" for c in report.checks),
            "passed_checks": sum(c["status"] == "pass" for c in report.checks),
        },
        "reasons": [
            c["message"] + (f" [{c['location']}]" if c.get("location") else "") for c in failures
        ],
        "checks": report.checks,
        "node_mapping": {safe_id(k): safe_id(v) for k, v in (mapping or {}).items()},
        "code_hashes": hashes,
        "disclaimer": DISCLAIMER,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Langflow export against a known-good reference.")
    parser.add_argument("--reference", required=True, type=Path, help="sanitized known-good Langflow export")
    parser.add_argument("--candidate", required=True, type=Path, help="generated Langflow export to check")
    parser.add_argument("--report", type=Path, help="write the JSON report to this file")
    parser.add_argument("--json", action="store_true", help="print the JSON report instead of text")
    args = parser.parse_args(argv)

    for label in ("reference", "candidate"):
        path = getattr(args, label)
        if not path.is_file():
            print(f"error: {label} file not found", file=sys.stderr)
            return 2

    result = validate(args.reference, args.candidate)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.report:
        try:
            args.report.write_text(text + "\n", encoding="utf-8")
        except OSError:
            print("error: could not write the report file", file=sys.stderr)
            return 2

    if args.json:
        print(text)
    else:
        print(result["result"])
        for reason in result["reasons"]:
            print(f"  - {reason}")
        print(f"\nNote: {DISCLAIMER}")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
