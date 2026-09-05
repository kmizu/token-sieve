#!/usr/bin/env python3
"""Measure raw output versus the entire Sieve JSON response using an optional token proxy."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--git-repo', type=Path, help='Also measure a real read-only git log command')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
try:
    import tiktoken
except ImportError:
    parser.error('Install tiktoken or run: uv run --no-project --with tiktoken python scripts/benchmark.py')
encoding = tiktoken.get_encoding('o200k_base')
root = Path(__file__).resolve().parents[1]
sieve = root / 'scripts' / 'sieve.py'
skill = (root / 'skills' / 'token-sieve' / 'SKILL.md').read_text()
cases = [
    ('synthetic_verbose_success', [sys.executable, '-c',
     'for i in range(5000): print(f"test_{i:05d} (suite.ExampleTests.test_{i:05d}) ... ok")\nprint("Ran 5000 tests in 4.2s\\nOK")']),
    ('synthetic_middle_failure', [sys.executable, '-c',
     'for i in range(5000):\n print(f"test_{i:05d} ... ok")\n if i==2500: print("FAIL: invariant_sentinel\\nTraceback (most recent call last):\\nAssertionError: expected 42 but received 41")\nprint("FAILED (failures=1)")\nraise SystemExit(1)']),
    ('tiny_output_control', [sys.executable, '-c', 'print("OK")']),
]
if args.git_repo:
    cases.append(('real_git_log_100', ['git', '-C', str(args.git_repo.resolve()), 'log', '-100', '--format=fuller']))
results = []
for name, command in cases:
    run = subprocess.run([sys.executable, str(sieve), 'run', '--', *command], capture_output=True, text=True)
    response = json.loads(run.stdout)
    if response.get('launch_error'):
        raise SystemExit(response['launch_error'])
    raw = Path(response['file']['path']).read_text(errors='replace')
    raw_tokens, result_tokens = len(encoding.encode(raw)), len(encoding.encode(run.stdout))
    if name == 'synthetic_middle_failure':
        assert run.returncode == 1 and 'invariant_sentinel' in run.stdout
    elif run.returncode != 0:
        raise SystemExit(f'{name}: exit {run.returncode}; inspect {response["file"]["path"]}')
    results.append({'case': name, 'exit_code': response['exit_code'],
                    'raw_bytes': response['file']['bytes'], 'response_bytes': len(run.stdout.encode()),
                    'raw_tokens': raw_tokens, 'response_tokens': result_tokens,
                    'reduction_percent': round(100 * (1 - result_tokens / max(1, raw_tokens)), 2),
                    'first_use_with_skill_tokens': result_tokens + len(encoding.encode(skill)),
                    'raw_artifact': response['file']['path']})
report = {'tokenizer': 'o200k_base; proxy, not verified GPT-6 billing tokenizer',
          'scope': 'Full raw text vs full JSON response, including metadata. Not billed API usage; host truncation/caching and follow-up reads are not measured.',
          'skill_tokens': len(encoding.encode(skill)), 'cases': results}
serialized = json.dumps(report, indent=2) + '\n'
if args.output:
    args.output.write_text(serialized)
print(serialized)
