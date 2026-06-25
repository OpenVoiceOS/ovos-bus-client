# TODO

## Open issues

- [ ] #183 Dependency Dashboard
- [ ] #161 allow overriding host and port via envvar
- [ ] #113 orjson: test if `.decode()` call can be removed
- [ ] #110 data structures for session-oriented global config parameters
- [ ] #108 New state tracking needs unit tests
- [ ] #92 introduce MalformedMessage exception
- [ ] #87 [FEATURE] Start using new gui-specific file extensions
- [ ] #63 Let `message.forward()` optionally keep the data
- [ ] #59 bounds for timeout value
- [ ] #37 Add Echo Client Util
- [ ] #33 document scripts

## Gaps

- [ ] Committed scratch artifact `.coverage` is tracked in git (not in `.gitignore`).
- [ ] Committed scratch artifact `downstream_report.txt` is tracked in git.
- [ ] `ovos_bus_client.egg-info/` present in working tree (gitignored, harmless if untracked).
- [ ] Tests, full CI suite (build-tests, coverage, license-check, lint, pip-audit, release_workflow, publish_stable, repo-health), and README all present — no structural gaps.

## Code TODOs

- `session.py:597` — Consider when to prune sessions; an event or callback scheduled.
- `client/collector.py:149` — check early return criteria.
- `apis/events.py:50` — Is a null name valid or should it raise an exception?
- `apis/gui.py:471` — Define enums for style and noticetype.
- `apis/gui.py:500` — Define enum for style.
- `apis/ocp.py:327` — this method will be deprecated.
- `apis/ocp.py:335` — support string uris.
- `apis/ocp.py:654` — ensure decent confidence match.
- `apis/ocp.py:690` — sleep is hacky, avoids a race condition.
