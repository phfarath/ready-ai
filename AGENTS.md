# Agent guide

## CI rules for agents and flaky tests

- Never delete, skip or weaken a test to make CI pass. If a test fails, report it with the output.
- A test that fails and then passes with no code change is flaky: quarantine it, open an issue with an owner, and keep it out of the merge gate.
- At most one automatic retry. Beyond that, investigate: shared data, hardcoded sleeps, external services, execution order.
- Workflow jobs must not poll or `sleep` waiting on another job: use `needs:` or completion events. Keep `concurrency` with `cancel-in-progress` on PRs.
