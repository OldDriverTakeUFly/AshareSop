# Example: First-Time Project Setup Session

Use this pattern when a repository has weak or partial local environment setup.

## Goal

Create a reproducible local setup without polluting the host machine.

## Good agent behavior

1. Inspect the repository for existing runtime, dependency, and service configuration.
2. Reuse existing conventions if they are coherent.
3. If missing, propose a minimal isolated setup.
4. Keep runtime versions explicit.
5. Keep dependencies local to the project.
6. Move heavy services into containers when practical.

## Example recommendation

- use `mise` for runtime pinning
- use `uv` and `.venv` for Python
- use `pnpm` for Node
- use `direnv` for automatic activation
- use `docker compose` for local services

## Example summary to return

"I found no existing runtime pinning or activation flow, so I set up a project-scoped environment strategy. Runtime versions are now pinned, Python dependencies live in `.venv`, Node dependencies stay in the project, and local services are expected to run through Compose rather than host installs."
