# Token Sieve

Keep verbose command output out of model context, with full logs available for inspection.

This Codex plugin contains one small skill and a dependency-free Python CLI. It captures command output on disk and returns a bounded head/diagnostic/tail excerpt with the original exit status, byte count and SHA-256. It can also summarize an existing text file and retrieve specific lines or literal matches. It makes no LLM or network calls.

## Install in Codex

Requirements: Git, Python 3.10+, and a Codex CLI with `codex plugin` support. Check with `python3 --version` and `codex plugin --help`. The plugin needs no API key, MCP server, or Python packages. See the [official Codex plugin documentation](https://learn.chatgpt.com/docs/plugins) for the host setup.

### macOS, Linux, or WSL

Clone this repository into a small local marketplace, register it, and install the plugin:

```sh
SIEVE_MARKETPLACE="$HOME/token-sieve-marketplace"
mkdir -p "$SIEVE_MARKETPLACE/plugins" "$SIEVE_MARKETPLACE/.agents/plugins"
git clone https://github.com/kmizu/token-sieve.git "$SIEVE_MARKETPLACE/plugins/token-sieve"
cat > "$SIEVE_MARKETPLACE/.agents/plugins/marketplace.json" <<'JSON'
{
  "name": "token-sieve-local",
  "interface": { "displayName": "Token Sieve" },
  "plugins": [{
    "name": "token-sieve",
    "source": { "source": "local", "path": "./plugins/token-sieve" },
    "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
JSON
codex plugin marketplace add "$SIEVE_MARKETPLACE"
codex plugin add token-sieve@token-sieve-local
codex plugin list --marketplace token-sieve-local
```

These are first-install commands; use a new directory if `~/token-sieve-marketplace` already contains other work. Keep the clone in place because the marketplace points to it. In WSL, run the commands inside WSL and use the Codex environment that shares that filesystem.

The repository itself is a plugin, not a marketplace, so `codex plugin marketplace add kmizu/token-sieve` is not the installation command. The local manifest above supplies that missing marketplace layer. The author's `token-sieve@personal` installation is also local to their machine; use `token-sieve@token-sieve-local` with these instructions.

### Start using it

Start a **new Codex thread** after installation so the skill is discovered. For the Codex app, use the same local environment as the CLI; restart the app if the skill does not appear. In the prompt box, type `$` and choose Token Sieve, or ask:

```text
Use $token-sieve for the test run. Inspect the full failure details if needed.
```

The skill wraps verbose commands when useful. It does not automatically intercept every tool call, and small outputs are better handled directly.

If `codex plugin` is unavailable, update the Codex CLI or use the standalone commands below. If Python is installed as `python` rather than `python3`, substitute that executable. Native Windows plugin installation is not covered by the shell commands above; WSL is the tested path here.

### Uninstall

```sh
codex plugin remove token-sieve@token-sieve-local
codex plugin marketplace remove token-sieve-local
```

The clone and captured logs are retained. Delete them separately only when you no longer need them.

## Standalone use

The same CLI works without installing the Codex plugin. From the cloned repository directory:

```sh
python3 scripts/sieve.py run --cwd /your/repo -- npm test
python3 scripts/sieve.py run --cwd /your/repo -- rg -n 'pattern' src
python3 scripts/sieve.py summarize /path/to/build.log
python3 scripts/sieve.py read /path/to/output.log --match 'FAIL' --context 3
python3 scripts/sieve.py read /path/to/output.log --start 100 --lines 40
```

Use absolute script paths when running outside the plugin directory. Arguments before `--` configure Sieve; arguments after it belong to the wrapped command. No shell expansion is performed. Noninteractive commands only: stdin is closed. Full stdout/stderr are combined without modifying their bytes. Logs are retained under `$CODEX_HOME/artifacts/token-sieve` or `~/.codex/artifacts/token-sieve`, in private per-run directories. Review before sharing: raw logs can contain secrets. There is no automatic deletion.

The default excerpt contains up to 2,400 text characters; JSON metadata and line annotations are additional. `--max-chars` changes that budget. The run timeout defaults to 900 seconds; `--timeout` changes it. Timeout returns 124, interruption 130, launch failure 127; otherwise Sieve preserves the command status (negative POSIX signals become shell-style 128+signal). On POSIX, timeout/interruption kills the command process group; on other platforms only the direct child is terminated. Interactive jobs and daemon launchers are not supported.

## Evidence and limits

This is **selective retrieval**, not lossless compression inside the model context. The full raw artifact is retained; its excerpt is deliberately incomplete. Diagnostic keywords are heuristic, not a parser for every test runner. Absence of an error excerpt does not prove success. Inspect omitted context for decisions that depend on it, and interpret exit codes according to the command (`rg`, for example, returns 1 for no matches).

Lines longer than 8KiB are scanned with bounded memory and their excerpts are marked clipped. Matching only inspects that line prefix; retrieve long-line content with an appropriate external reader. Summary text removes ANSI sequences; `read` preserves text except line endings, invalid UTF-8 replacement and marked clipping. Files should be stable during inspection; hashes describe the raw file read, not a transactionally locked snapshot.

The plugin is opt-in through its skill. It does not intercept every shell call, rewrite other MCP outputs, shrink model reasoning, or change billing. Small outputs should be read directly because wrapper metadata and skill loading can cost more than the original. Ordinary approvals and sandbox boundaries still apply; this command runner is not a security boundary.

## Verify and measure

```sh
python3 -m unittest discover -s tests -v
uv run --no-project --with tiktoken python scripts/benchmark.py
# Optional read-only real repository example:
uv run --no-project --with tiktoken python scripts/benchmark.py --git-repo /your/repo --output /tmp/sieve-metrics.json
```

The benchmark runs two synthetic verbose logs, a tiny-output control, and optionally a real `git log`. It counts the **entire JSON response**, not just the selected text, and reports first-use cost including the skill. `o200k_base` is a measurement proxy, not a verified GPT-6 billing tokenizer. Actual benefit depends on the host's existing truncation, caching, number of calls and follow-up retrievals. The normal CLI has no tiktoken dependency.

### Sample measurement (2026-09-05)

| Input | Raw tokens | Full response tokens | Reduction |
|---|---:|---:|---:|
| Synthetic 5,000-test success log | 85,014 | 386 | 99.55% |
| Synthetic middle failure log | 35,032 | 375 | 98.93% |
| Actual 100-commit Git history | 25,224 | 818 | 96.76% |
| Tiny `OK` control | 2 | 160 | Increased — use direct execution |

The skill adds 522 proxy tokens on first load. These observations compare retained raw text to the response; a host that already caps tool output at a few thousand tokens has a smaller baseline. Follow-up reading costs additional tokens. They establish output reduction on these examples, not a universal performance or bill reduction.
