# Example: Recovering from Dependency Conflicts

Use this pattern when a local machine has drifted into a mixed environment state.

## Common symptoms

- the wrong Python version is active
- `pip` installs appear to affect multiple projects
- Node commands work in one repo and fail in another
- local databases depend on undocumented host setup

## Recovery model

1. Separate runtime issues from dependency issues from service issues.
2. Re-establish the intended runtime version first.
3. Rebuild project-local dependency state.
4. Move services to a documented containerized workflow if possible.
5. Remove stale or conflicting state only after identifying what it belongs to.

## Bad recovery pattern

Avoid stacking more global fixes on top of a broken setup.

Examples of bad fixes:

- editing shell startup files repeatedly
- global package installs to mask missing local setup
- switching package managers midstream without cleanup

## Example summary to return

"The environment was mixing host-level and project-level state. I treated the problem in layers by restoring the intended runtime first, then rebuilding local dependencies, and keeping services isolated from the host."
