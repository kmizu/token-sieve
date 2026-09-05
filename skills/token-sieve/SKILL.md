---
name: token-sieve
description: Reduce context from verbose tests, builds, searches, or large text files using bounded excerpts and recoverable full logs.
---

# Token Sieve

Use the bundled Python script when a command or file is likely to produce much more text than the task needs. Run small, already-bounded commands directly. This plugin does not intercept other tools or alter model billing.

Resolve `scripts/sieve.py` from this plugin's root (two directories above this skill folder). Use an absolute script path in shell calls.

```sh
python3 /path/to/token-sieve/scripts/sieve.py run --cwd /repo -- npm test
python3 /path/to/token-sieve/scripts/sieve.py run --cwd /repo -- rg -n 'pattern' src
python3 /path/to/token-sieve/scripts/sieve.py summarize /path/to/existing.log
python3 /path/to/token-sieve/scripts/sieve.py read /path/to/full.log --match 'ERROR' --context 3
python3 /path/to/token-sieve/scripts/sieve.py read /path/to/full.log --start 120 --lines 60
```

`run` executes argv directly, preserving the command's exit status. Its combined stdout/stderr is retained in a private local artifact. Explicit shell syntax requires an explicit shell invocation and the same authorization as running it directly. It is not a sandbox or approval bypass. Use the host's normal approval mechanism when required; do not wrap a command to evade a rejected action.

The response reports exit status, raw-file location/hash/size, and a bounded head/diagnostic/tail excerpt. Omitted content is marked. Diagnostic selection is heuristic: absence of a diagnostic does not prove success. A zero exit code only reports process status. Inspect relevant ranges before concluding that tests, warnings, or a review are complete. Commands such as `rg` use nonzero codes for non-error outcomes; interpret the original command's semantics.

`read` returns exact selected line text without ANSI cleanup, with explicit truncation and next-line information. Narrow the query or page forward when needed. Keep raw logs private; they can contain source code or secrets. Remove artifacts only when the user no longer needs them.

Defaults: 2,400 excerpt characters; 900-second run timeout. Change with `--max-chars` or `run --timeout`. Full logs are written to `$CODEX_HOME/artifacts/token-sieve` (or `~/.codex/artifacts/token-sieve`). No API calls or external dependencies are required.
