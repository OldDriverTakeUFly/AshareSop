# AGENTS.md

This repository expects coding agents to follow the local development environment skill for any environment-related work.

## Default Rule

For tasks involving local setup, dependency installation, runtime selection, environment repair, toolchain isolation, local services, or project switching, agents must read and follow:

- `skills/local-development-environment/SKILL.md`

Companion materials are available here:

- `skills/local-development-environment/checklists/preflight.md`
- `skills/local-development-environment/checklists/cleanup.md`
- `skills/local-development-environment/examples/setup-session.md`
- `skills/local-development-environment/examples/switch-projects.md`
- `skills/local-development-environment/examples/troubleshoot-conflicts.md`
- `skills/local-development-environment/references/tooling-matrix.md`
- `skills/local-development-environment/README.zh-CN.md`

## When This Applies

Use the skill whenever the task includes any of the following:

- setting up a local development environment
- switching between projects with different runtime requirements
- fixing dependency conflicts or mixed environments
- choosing Python, Node.js, Java, Ruby, or mixed-language runtime strategies
- deciding whether something should be local, global, or containerized
- documenting team conventions for local environment management

## Required Agent Behavior

The requirements below are a non-exhaustive summary. They do not replace `skills/local-development-environment/SKILL.md`.

Agents working on environment-related tasks must:

1. inspect the repository before making changes
2. prefer existing repository conventions when they are coherent
3. prefer project-local isolation over host-level fixes
4. avoid global installs unless the project or user explicitly requires them
5. verify the resulting environment after setup or repair
6. report what was created, reused, skipped, or left unresolved

## Guardrails

This skill is guidance for environment-related work only.

Agents must not:

- use the skill as a reason to make unrelated repository changes
- overwrite environment files without first reading and understanding them
- introduce new environment tools without explaining why they are needed
- claim an environment is reproducible without checking versions, lockfiles, or startup paths

## Post-Report Push Rule (MANDATORY)

After **every** research report is completed and verified in `docs/`, the agent MUST:

1. **Verify** the report file: check chapter completeness, no PART markers, proper start/end
2. **Commit** with semantic style: `feat(docs): add {report-name}`
3. **Push** to `origin/master` immediately
4. **Confirm** push succeeded before reporting completion to user

This is a non-negotiable step. A report is not "done" until it is on GitHub.

Commit message format (match existing repo style):
```
feat(docs): add {short-description}
```

Always include agent attribution footer:
```
Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
```

Remote: `origin` → `git@github.com:OldDriverTakeUFly/AshareSop.git`

## Source of Truth

If this file and the skill differ in detail, treat `skills/local-development-environment/SKILL.md` as the source of truth for environment-management behavior.
