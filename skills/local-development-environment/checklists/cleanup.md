# Cleanup Checklist

Use this checklist when finishing environment work or recovering from a mixed setup.

## Before leaving the project

- [ ] Confirm the intended runtime version is active
- [ ] Confirm dependencies install and resolve locally
- [ ] Confirm local services start through the documented path
- [ ] Confirm any added files are intentional and documented

## If repairing a broken environment

- [ ] Identify whether the breakage came from runtime mismatch, dependency mismatch, or service mismatch
- [ ] Remove stale temporary state only when safe and explain what was removed
- [ ] Keep project-local envs and lockfiles as the source of truth
- [ ] Avoid “fixing” by adding more global overrides

## Report back

- [ ] State what was created, reused, repaired, or skipped
- [ ] State any remaining manual steps
- [ ] State unresolved risks or follow-up recommendations
