# orc

`orc` is a quota-aware coding-agent orchestrator. Milestone 1 provides one safe
Claude Code lane: branch a clean Git repository, run Claude, independently verify
the result, and retry with higher effort plus exact verification feedback.

```sh
uv sync
cd examples/sample-python
../../.venv/bin/orc "make the tests pass"
```

Run artifacts and the advisory ledger are persisted under the target repository's
`.orc/` directory. Configuration is discovered from `orc.toml` in the target or
one of its parent directories.
