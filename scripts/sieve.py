#!/usr/bin/env python3
"""Bounded context excerpts backed by complete local command logs. Python 3.10+."""
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
DIAGNOSTIC = re.compile(
    r'\b(?:error|fail|failed|failure|fatal|panic|traceback|exception|warning|warn|'
    r'passed|tests? run|test result|BUILD SUCCESS|BUILD FAILURE)\b', re.I)


def bounded_lines(path):
    """Yield (number, first 8KiB decoded, clipped) without allocating huge lines."""
    with path.open('rb') as stream:
        number = 0
        while True:
            part = stream.readline(8193)
            if not part:
                break
            number += 1
            clipped = len(part) > 8192
            if not part.endswith(b'\n') and len(part) == 8193:
                while True:
                    rest = stream.readline(65536)
                    if not rest or rest.endswith(b'\n'):
                        break
            yield number, part[:8192].decode('utf-8', errors='replace').rstrip('\r\n'), clipped


def identity(path):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            size += len(chunk)
            digest.update(chunk)
    return {'path': str(path.resolve()), 'bytes': size, 'sha256': digest.hexdigest()}


def excerpt(rows, max_chars, clean=False):
    selected = []
    used = 0
    incomplete = False
    for number, text, clipped in rows:
        if clean:
            text = ANSI.sub('', text)
        available = max_chars - used
        if available <= 0:
            incomplete = True
            break
        # Reserve part of a summary for subsequent evidence, including its tail.
        cap = min(240, available) if clean else available
        if len(text) > cap:
            text = text[:cap]
            clipped = True
        selected.append({'line': number, 'text': text, 'clipped': clipped})
        used += len(text)
        incomplete |= clipped
    return selected, incomplete


def summarize(path, max_chars):
    head, diagnostics, tail = [], deque(maxlen=10), deque(maxlen=5)
    first_diagnostics = []
    diagnostic_count = 0
    count = 0
    # Take bounded samples; the unmodified raw artifact is the source of truth.
    for row in bounded_lines(path):
        count = row[0]
        if len(head) < 3:
            head.append(row)
        tail.append(row)
        if DIAGNOSTIC.search(ANSI.sub('', row[1])):
            diagnostic_count += 1
            if len(first_diagnostics) < 3:
                first_diagnostics.append(row)
            diagnostics.append(row)
    # Reserve space for each group so early repeated warnings cannot hide the tail.
    recent = list(reversed(diagnostics))
    diagnostic_priority = []
    for i in range(max(len(first_diagnostics), len(recent))):
        if i < len(first_diagnostics):
            diagnostic_priority.append(first_diagnostics[i])
        if i < len(recent):
            diagnostic_priority.append(recent[i])
    groups = [('head', head, max_chars // 5),
              ('diagnostics', diagnostic_priority, max_chars * 3 // 5),
              ('tail', list(reversed(tail)), max_chars - max_chars // 5 - max_chars * 3 // 5)]
    picked = {}
    clipped = False
    for group, rows, budget in groups:
        unique = {row[0]: row for row in rows if row[0] not in picked}
        selected, cut = excerpt(unique.values(), budget, clean=True)
        clipped |= cut
        for row in selected:
            row['group'] = group
            picked[row['line']] = row
    return {'file': identity(path), 'lines': count,
            'selection': 'heuristic head/diagnostic/tail; not a complete diagnostic report',
            'diagnostic_matching_lines': diagnostic_count,
            'omitted_lines': count - len(picked),
            'incomplete': clipped or len(picked) < count,
            'excerpt': [picked[n] for n in sorted(picked)]}


def read_range(path, start, lines, match, context, max_chars):
    rows = []
    # Literal matches; no regex evaluation of untrusted patterns.
    previous = deque(maxlen=context)
    through = 0
    has_more = False
    next_line = start
    last_added = 0
    used = 0
    for row in bounded_lines(path):
        n, text, _ = row
        if n < start:
            continue
        if match is not None and match in text:
            through = n + context
            candidates = list(previous) + [row]
        elif match is None or n <= through:
            candidates = [row]
        else:
            candidates = []
        for candidate in candidates:
            if candidate[0] <= last_added:
                continue
            if len(rows) >= lines or used >= max_chars:
                has_more = True
                next_line = candidate[0]
                break
            rendered, _ = excerpt([candidate], max_chars - used)
            rows.extend(rendered)
            used += len(rendered[0]['text'])
            last_added = candidate[0]
            next_line = last_added + 1
        if has_more:
            break
        previous.append(row)
    return {'file': str(path.resolve()), 'selection': 'literal match with context' if match is not None else 'line range',
            'match': match, 'excerpt': rows, 'has_more': has_more,
            'next_line': next_line if has_more else None,
            'clipped_lines': [r['line'] for r in rows if r['clipped']],
            'note': 'Long lines are clipped; use a narrower external reader for their full contents.'}


def run_command(args):
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        raise ValueError('run requires a command after --')
    codex = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    parent = codex / 'artifacts' / 'token-sieve'
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    log = artifact / 'output.log'
    started = time.monotonic()
    timed_out = False
    launch_error = None
    with log.open('wb') as stream:
        log.chmod(0o600)
        try:
            process = subprocess.Popen(command, cwd=args.cwd, stdout=stream,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=(os.name == 'posix'))
            try:
                code = process.wait(timeout=args.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                timed_out = isinstance(error, subprocess.TimeoutExpired)
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()
                code = 124 if timed_out else 130
        except OSError as error:
            launch_error = str(error)
            code = 127
    result = summarize(log, args.max_chars)
    result.update({'exit_code': code, 'timed_out': timed_out,
                   'launch_error': launch_error, 'elapsed_seconds': round(time.monotonic() - started, 3)})
    meta = artifact / 'result.json'
    meta.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    meta.chmod(0o600)
    return result, (128 - code if code < 0 else code)


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('run', help='Capture an argv command and return bounded evidence')
    run.add_argument('--cwd', type=Path)
    run.add_argument('--timeout', type=positive, default=900)
    run.add_argument('--max-chars', type=positive, default=2400)
    run.add_argument('command', nargs=argparse.REMAINDER)
    summary = commands.add_parser('summarize', help='Summarize an existing log without executing it')
    summary.add_argument('path', type=Path)
    summary.add_argument('--max-chars', type=positive, default=2400)
    read = commands.add_parser('read', help='Read a line range or literal matches with context')
    read.add_argument('path', type=Path)
    read.add_argument('--max-chars', type=positive, default=2400)
    read.add_argument('--start', type=positive, default=1)
    read.add_argument('--lines', type=positive, default=40)
    read.add_argument('--match')
    read.add_argument('--context', type=int, choices=range(0, 21), default=2)
    args = parser.parse_args()
    try:
        if args.action == 'run':
            result, code = run_command(args)
        elif args.action == 'summarize':
            result, code = summarize(args.path, args.max_chars), 0
        else:
            result = read_range(args.path, args.start, args.lines, args.match, args.context, args.max_chars)
            code = 0
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
        return code
    except (OSError, ValueError) as error:
        print(json.dumps({'error': str(error)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
