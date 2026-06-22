"""Prompt template registry — PromptTemplate dataclass + PromptRegistry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptTemplate:
    name: str
    version: str
    system: str
    user_template: str
    expected_output_schema: dict


class PromptRegistry:
    """Registry for prompt templates keyed by (name, version)."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, PromptTemplate]] = {}

    def register(self, template: PromptTemplate) -> None:
        versions = self._store.setdefault(template.name, {})
        versions[template.version] = template

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        if name not in self._store:
            raise KeyError(f"Prompt template '{name}' not found")
        versions = self._store[name]
        if version is None:
            latest = sorted(versions.keys())[-1]
            return versions[latest]
        if version not in versions:
            raise KeyError(
                f"Prompt template '{name}' version '{version}' not found"
            )
        return versions[version]

    def list_names(self) -> list[str]:
        return list(self._store.keys())


default_registry = PromptRegistry()
