# Preflight Checklist

Use this checklist before changing a local development environment.

## Detect existing configuration

- [ ] Check for runtime version files such as `.mise.toml`, `.tool-versions`, `.nvmrc`, `.python-version`
- [ ] Check for dependency files such as `pyproject.toml`, `uv.lock`, `requirements.txt`, `package.json`, lockfiles
- [ ] Check for activation files such as `.envrc`, `.env.example`
- [ ] Check for service definitions such as `docker-compose.yml`, `compose.yml`, `Dockerfile`
- [ ] Check whether the repo already documents setup steps

## Detect current risk areas

- [ ] Is the repo already using more than one runtime manager?
- [ ] Is there evidence of global installs being relied on?
- [ ] Are service dependencies undocumented or host-installed?
- [ ] Are runtime versions implicit rather than pinned?
- [ ] Are lockfiles missing where they should exist?

## Decide the approach

- [ ] Reuse the existing environment strategy if it is coherent
- [ ] If there is no strategy, introduce the smallest viable isolated setup
- [ ] Prefer project-local state over host-level state
- [ ] Identify what needs verification after the change
