# Example: Switching Between Projects with Conflicting Runtimes

Use this pattern when the user works across multiple repositories that require different versions of Python, Node, or other runtimes.

## Goal

Ensure switching directories changes runtime context safely without breaking neighboring projects.

## Good agent behavior

1. Confirm each project has explicit runtime version declarations.
2. Use a directory-aware runtime manager.
3. Keep each project's dependencies inside its own environment.
4. Avoid host-level aliases or shell hacks as the main solution.

## Preferred outcome

- entering project A activates runtime A
- entering project B activates runtime B
- Python dependencies do not leak across `.venv` directories
- Node version switching does not depend on manual shell edits

## Example summary to return

"The conflict came from shared host state, not from the repositories themselves. The fix is to pin runtimes per project and keep dependencies local, so moving between directories switches context instead of overwriting it."
