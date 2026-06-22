---
name: local-development-environment
description: Use this skill when setting up, switching, repairing, or documenting local development environments so dependencies stay isolated and projects do not contaminate each other. Trigger phrases include 'set up local dev env', 'avoid dependency conflicts', 'manage Python/Node versions', 'switch between projects', and 'clean up my environment'.
---

# Local Development Environment Management

This skill gives agents a consistent way to manage local development environments without causing dependency conflicts, runtime drift, or host-machine pollution.

It is designed to be portable across coding agents. The goal is not to force one exact toolchain, but to enforce a stable operating model:

1. keep host systems as clean as possible
2. keep runtime versions project-scoped
3. keep dependencies project-local
4. containerize heavy services when practical
5. make environment changes inspectable and reversible

## Purpose

Use this skill when an agent needs to:

- set up a project locally for the first time
- switch between projects with different runtime requirements
- repair a broken or mixed environment
- recommend tooling for dependency isolation
- document how a team should manage local environments

This skill covers:

- runtime version management
- dependency isolation
- local service isolation
- environment activation and switching
- cleanup and recovery

This skill does not assume one language. It should work for Python, Node.js, Java, Go, Ruby, and mixed-language repositories.

## Agent Contract

Before making any environment change, the agent must:

1. inspect the repository for existing environment configuration
2. prefer existing project conventions over introducing a new tool
3. avoid global installs unless explicitly required
4. state what it detected, what it plans to change, and why

While working, the agent must:

1. keep runtime changes project-scoped when possible
2. keep dependencies inside the project or inside an isolated env
3. verify the active runtime and dependency state after setup
4. avoid mixing multiple competing environment managers unless the repo already does so intentionally

When finishing, the agent must:

1. report what was created, reused, or skipped
2. report any assumptions or unresolved risks
3. leave the environment in a reusable state for the next session

## Core Rules

### Rule 1: Inspect before acting

Always check for existing files before proposing or changing environment setup. Common signals include:

- `.mise.toml`, `.tool-versions`, `.nvmrc`, `.node-version`
- `pyproject.toml`, `uv.lock`, `requirements.txt`, `.python-version`, `.venv/`
- `package.json`, `pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`
- `docker-compose.yml`, `compose.yml`, `Dockerfile`
- `.envrc`, `.env.example`
- language-specific version files and lockfiles

If the repository already has an environment strategy, follow it unless there is a clear problem.

### Rule 2: Prefer project-local isolation

Use the smallest safe scope.

- runtime version selection should be directory-scoped when possible
- dependencies should live inside the project environment
- local services should run in containers when practical

Preferred order of isolation:

1. project-local environment
2. user-level version manager
3. system package manager

### Rule 3: Avoid global mutable state

Do not install project dependencies globally unless the user explicitly asks for it or the tool requires it.

Avoid:

- `pip install` into the global interpreter
- broad global npm installs for project-specific tools
- editing shell startup files just to make one repo work
- mixing multiple package managers for the same dependency set without a clear reason

### Rule 4: Lock what can drift

Prefer workflows that preserve reproducibility.

- keep runtime versions explicit
- keep dependency lockfiles checked in when the ecosystem supports them
- avoid undocumented local-only steps

### Rule 5: Separate app dependencies from service dependencies

Differentiate between:

- runtime versions, such as Python or Node
- project dependencies, such as pip or pnpm packages
- local services, such as PostgreSQL, Redis, Elasticsearch, Kafka

Services should usually be isolated with containers rather than installed into the host.

## Standard Workflow

### Step 1: Preflight

Inspect the repo and identify:

- languages in use
- runtime version declarations
- dependency managers
- lockfiles
- local service requirements
- existing activation flows

Then state the detected strategy in one short paragraph before changing anything.

### Step 2: Choose the minimum-change path

Use the repo's existing approach if present. If none exists, prefer a simple, modern stack such as:

- runtime manager: `mise` or `asdf`
- Python env: `uv` with `.venv`
- Node dependencies: `pnpm`
- local services: `docker compose`
- directory activation: `direnv`

Do not introduce all of these by default. Introduce only what the project needs.

### Step 3: Create or repair the isolated environment

Typical actions may include:

- install or select the required runtime version
- create `.venv` for Python projects
- install Node dependencies into local project state
- prepare `.envrc` for safe activation
- bring up dependent services with containers

### Step 4: Verify

After setup, verify:

- active runtime versions
- expected dependency manager behavior
- ability to run the project's standard install, test, or dev command
- service availability when applicable

### Step 5: Report and preserve

Summarize:

- what environment strategy is now in place
- which files were created or updated
- what the next agent or developer should run
- what remains manual or unresolved

## Decision Rules

### If multiple runtime managers are present

Prefer the one already used by the repo.

Only propose consolidation if the current setup is clearly broken or contradictory.

### If the repo has no environment files

Recommend a minimal setup rather than a large framework rollout.

Good baseline:

- `.mise.toml` for runtimes
- `.envrc` for activation
- `.venv` for Python
- lockfiles for dependencies
- `docker-compose.yml` for local services

### If dependencies conflict across projects

Do not solve this by making the host more global. Solve it by making each project more isolated.

Typical fixes:

- move Python work into a project `.venv`
- pin and auto-switch Node versions per repo
- move databases and queues into containers
- remove undocumented shell aliases or global overrides

### If a global tool is unavoidable

Install only the minimum bootstrap layer globally. Good examples are:

- version managers
- container runtime
- `direnv`

Do not treat language package managers as a substitute for environment isolation.

## Recommended Defaults by Layer

### Host layer

Keep global installs minimal. Good global candidates:

- `git`
- `docker`
- one runtime manager such as `mise`
- `direnv`

### Runtime layer

Prefer explicit runtime pinning:

- Python version in `.mise.toml` or equivalent
- Node version in `.mise.toml` or equivalent
- avoid silent dependence on whatever is preinstalled on the host

### Dependency layer

Prefer project-local dependency state:

- Python in `.venv`
- Node in `node_modules`
- lockfiles committed when appropriate

### Service layer

Prefer containerized local services for heavier dependencies.

Examples:

- PostgreSQL
- Redis
- Elasticsearch
- Kafka

## Enforcement Norms

An agent following this skill must:

- inspect before changing
- justify any new tool it introduces
- avoid silent global changes
- verify the resulting environment
- document follow-up commands for the user

An agent following this skill must not:

- install project dependencies into the global interpreter or global package space without explicit user approval
- overwrite existing environment files without first reading and understanding them
- mix dependency managers carelessly
- claim the environment is reproducible without checking versions and lockfiles

## Success Criteria

This skill has been applied correctly when:

- switching projects does not break another project's dependencies
- the active runtime is predictable inside the repo
- dependency installation is local to the project
- services are isolated from the host when appropriate
- the next developer or agent can reproduce the environment from repository files

## Extension Points

This skill is intentionally generic. It can be extended later with:

- language-specific overlays
- company policy modules
- OS-specific bootstrap guides
- security or supply-chain review checklists

See the companion files in this directory for checklists, examples, and a tooling matrix.
