# davis_analyzer/cardgen/materialize.py
"""$fact 递归物化:spec 中值恰为 {"$fact": id} 的节点 → fact.display。"""
from __future__ import annotations

import hashlib
import json

from davis_analyzer.cardgen.types import Fact, Failure


def spec_digest(spec: dict) -> str:
    payload = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def materialize_spec(spec: dict, facts: dict[str, Fact]) -> tuple[dict | None, list[Failure]]:
    failures: list[Failure] = []

    def walk(node: object, path: str) -> object:
        if isinstance(node, dict):
            if set(node.keys()) == {"$fact"}:
                fid = node["$fact"]
                if fid not in facts:
                    failures.append(Failure("materialize", "", path, f"未知事实 id: {fid}"))
                    return None
                return facts[fid].display
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x, f"{path}[{i}]") for i, x in enumerate(node)]
        return node

    result = walk(spec, "")
    if failures:
        return None, failures
    return result, failures  # type: ignore[return-value]
