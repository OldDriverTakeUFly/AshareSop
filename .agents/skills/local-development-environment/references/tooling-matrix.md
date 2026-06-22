# Tooling Matrix

This matrix helps agents choose reasonable environment-management defaults without assuming they must always introduce new tools.

| Layer | Preferred default | Why | Notes |
| --- | --- | --- | --- |
| Runtime management | `mise` | One tool for multiple runtimes | `asdf` is also acceptable if already present |
| Directory activation | `direnv` | Auto-load per-project context | Should not be used to hide broken env design |
| Python dependencies | `uv` + `.venv` | Fast, project-local, modern workflow | Reuse `venv` or Poetry if repo already uses them |
| Node dependencies | `pnpm` | Project-local and reproducible | Reuse npm or yarn if repo already standardizes on them |
| Local services | `docker compose` | Isolates heavy service dependencies | Prefer over host-installed databases when practical |
| Java versions | `mise` or `sdkman` | Explicit per-project versioning | Prefer existing repo/team convention |
| Ruby versions | `mise` or `rbenv` | Keeps versions isolated | Prefer existing repo/team convention |

## Selection rule

Introduce a new tool only if:

1. the repository does not already have a coherent alternative
2. the new tool materially improves isolation or reproducibility
3. the resulting setup is easy for the next developer or agent to understand
