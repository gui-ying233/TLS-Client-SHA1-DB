"""Run a bounded, resumable subset of native probes without GitHub Actions.

The repository, observations and evidence schemas are unmodified. No Git push,
commits, synthetic fingerprint data, or changes to old observations occur here.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpdb.planner import load_config, plan
from fpdb.persistence import persist
STATE = ROOT / '.fpdb-local'
NODE_BASE = Path('/cache/node')
_LOCKS: dict[str, Lock] = {}
_LOCKS_GUARD = Lock()


def resource_lock(key: str) -> Lock:
    # The lock table itself is protected: two workers must get the same lock.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, Lock())


def probe_lock(target: dict):
    # npm --prefix overwrites a shared node_modules tree; hold the lock through
    # both installation AND the TLS probe so package versions cannot change.
    prepare = target.get('adapter', {}).get('prepare', [])
    if '--prefix' in prepare:
        index = prepare.index('--prefix') + 1
        if index < len(prepare):
            return resource_lock('npm:' + prepare[index])
    return nullcontext()


def run(args: list[str], *, env: dict | None = None) -> None:
    print('+', ' '.join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True, text=True)


def node_binary(version: str) -> Path:
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid Node.js version')
    dest = NODE_BASE / ('v' + version)
    node = dest / 'bin/node'
    if not node.exists():
        name = f'node-v{version}-linux-x64.tar.xz'
        base = f'https://nodejs.org/dist/v{version}/'
        print('Downloading and verifying', name, flush=True)
        manifest = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60).read().decode()
        line = next((x for x in manifest.splitlines() if x.endswith('  ' + name)), None)
        if line is None:
            raise ValueError('Node.js release archive not found in checksum manifest')
        expected = line.split()[0]
        archive = NODE_BASE / name
        digest = hashlib.sha256()
        with urllib.request.urlopen(base + name, timeout=180) as source, archive.open('wb') as sink:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                sink.write(chunk)
        if digest.hexdigest() != expected:
            archive.unlink(missing_ok=True)
            raise ValueError('Node.js archive checksum mismatch')
        # Extract a trusted official tarball into the writable, persistent cache.
        tmp = NODE_BASE / ('.unpack-' + version)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        try:
            with tarfile.open(archive, 'r:xz') as tar:
                # No absolute paths, link traversal or archive writes outside cache.
                members = tar.getmembers()
                prefix = f'node-v{version}-linux-x64/'
                if any(not (m.name == prefix.rstrip('/') or m.name.startswith(prefix))
                       or not (m.isfile() or m.isdir() or m.issym())
                       for m in members):
                    raise ValueError('Unexpected Node.js archive layout')
                # Official upstream archive is trusted only after SHA256 verification.
                tar.extractall(tmp, filter='data')
            (tmp / prefix.rstrip('/')).rename(dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            archive.unlink(missing_ok=True)
    actual = subprocess.check_output([str(node), '--version'], text=True).strip()
    if actual != 'v' + version:
        raise ValueError(f'Node.js runtime {actual} != v{version}')
    return node


def python_binary(version: str) -> Path:
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid Python version')
    run(['uv', 'python', 'install', '--no-config', version])
    path = subprocess.check_output(
        ['uv', 'python', 'find', '--no-config', '--managed-python', version], text=True).strip()
    if not path:
        raise ValueError('UV did not provide a Python interpreter')
    actual = subprocess.check_output(
        [path, '-c', 'import platform; print(platform.python_version())'], text=True).strip()
    if actual != version:
        raise ValueError(f'Python runtime {actual} != {version}')
    return Path(path)


def supported(target: dict) -> bool:
    if target.get('runner') != 'ubuntu-24.04' or target.get('platform') != 'linux-x86_64':
        return False
    if target.get('language') in ('node', 'python') and not target['id'].startswith('env-'):
        return True
    return target['id'] in {
        'ubuntu-curl', 'ubuntu-wget', 'ubuntu-openssl'}


def collect_one(case: dict, target: dict, inbox: Path) -> bool:
    language, version = case['language'], case['runtime_version']
    env = os.environ.copy()
    if language == 'node':
        with resource_lock('node:' + version):
            node = node_binary(version)
        env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    elif language == 'python':
        with resource_lock('python:' + version):
            env['FPDB_PYTHON_BIN'] = str(python_binary(version))
    elif case['target'] not in {'ubuntu-curl', 'ubuntu-wget', 'ubuntu-openssl'}:
        raise ValueError('Unsupported local Docker target')
    # Explicit case identity is always verified again by fpdb.collector.
    env['FPDB_CASES_JSON'] = json.dumps([case], separators=(',', ':'))
    with probe_lock(target):
        code = subprocess.run([sys.executable, '-m', 'fpdb.cli', 'collect',
                               '--cases-env', 'FPDB_CASES_JSON', '--output-dir', str(inbox)],
                              cwd=ROOT, env=env, check=False).returncode
    if code:
        print(f'FAILED case={case["id"]} target={target["id"]}', flush=True)
    return code == 0 and (inbox / (case['id'] + '.json')).is_file()


def main() -> int:
    parser = argparse.ArgumentParser(description='Docker-hosted TLS fingerprint collector')
    parser.add_argument('--target', help='Existing target id')
    parser.add_argument('--all-supported', action='store_true', help='Round-robin supported targets')
    parser.add_argument('--mode', choices=('incremental', 'historical', 'full'), default='incremental')
    parser.add_argument('--jobs', type=int, default=4, help='Planner batch budget (1..200)')
    parser.add_argument('--max-cases', type=int, default=32, help='Maximum cases per local invocation')
    parser.add_argument('--parallel', type=int, default=1, help='Concurrent Case workers (1..16; default 1)')
    parser.add_argument('--recheck', action='store_true', help='Explicitly repeat already collected cases')
    parser.add_argument('--list-targets', action='store_true')
    parser.add_argument('--reindex', action='store_true', help='Rebuild index from remaining observations')
    args = parser.parse_args()
    if args.reindex:
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / 'docker-runner.lock').open('a+') as maintenance_lock:
            try:
                fcntl.flock(maintenance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error('Another Docker collector is using this checkout; finish it first')
            from fpdb.index import build as build_index
            print('index_changed=' + str(build_index(ROOT)).lower())
        return 0
    config = load_config(ROOT)
    targets = [t for t in config['targets'] if t.get('enabled') and supported(t)]
    if args.list_targets:
        for t in targets:
            print(t['id'])
        return 0
    if args.all_supported and args.target:
        parser.error('Use either --target or --all-supported')
    selected = (targets if args.all_supported else
                [t for t in targets if t['id'] == args.target])
    if not selected:
        parser.error('Supply --target or --all-supported (see --list-targets)')
    if not 1 <= args.jobs <= 200 or not 1 <= args.max_cases <= 2000:
        parser.error('--jobs must be 1..200 and --max-cases must be 1..2000')
    if not 1 <= args.parallel <= 16:
        parser.error('--parallel must be 1..16')
    inbox = STATE / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    # Never let two separate Docker invocations mutate the same inbox/data tree.
    # In-process threads share this lock and persist only after all workers end.
    run_lock = (STATE / 'docker-runner.lock').open('a+')
    try:
        fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another Docker collector is using this checkout; finish it first')
    # Recover a previous interrupted invocation before planning to avoid duplicate samples.
    recovered = list(inbox.rglob('*.json'))
    if recovered:
        changed, processed = persist(ROOT, inbox)
        print(f'recovered_artifacts={processed} changed_records={changed}', flush=True)
        for p in inbox.iterdir():
            if p.suffix in ('.json', '.skip'):
                p.unlink()
    queues = []
    discovery_errors = 0
    for target in selected:
        try:
            pending = plan(ROOT, mode=args.mode, target_filter=target['id'],
                           recheck=args.recheck, batch_size=args.jobs)
            queues.append((target, iter(pending)))
        except (OSError, ValueError, KeyError) as exc:
            discovery_errors += 1
            print(f'FAILED planning target={target["id"]}: {exc}', file=sys.stderr, flush=True)
    # Interleave targets so a large cross-product cannot starve other clients.
    work = []
    while len(work) < args.max_cases and queues:
        next_queues = []
        for target, pending in queues:
            try:
                case = next(pending)
            except StopIteration:
                continue
            work.append((target, case))
            next_queues.append((target, pending))
            if len(work) >= args.max_cases:
                break
        queues = next_queues
    print(f'planned_cases={len(work)} targets={len(selected)} mode={args.mode} parallel={args.parallel}', flush=True)
    succeeded, failed = 0, 0

    def worker(i: int, target: dict, case: dict) -> bool:
        print(f'[{i}/{len(work)}] {target["id"]} {case["runtime_version"]} / {case["client_version"]}', flush=True)
        try:
            ok = collect_one(case, target, inbox)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            print(f'FAILED setup case={case["id"]}: {exc}', file=sys.stderr, flush=True)
            ok = False
        print(f'[{i}/{len(work)}] {"OK" if ok else "FAILED"} case={case["id"]}', flush=True)
        return ok

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        tasks = [pool.submit(worker, i, target, case)
                 for i, (target, case) in enumerate(work, start=1)]
        for future in as_completed(tasks):
            try:
                ok = future.result()
            except Exception as exc:
                # Do not lose other successful artifacts due to one unexpected error.
                print(f'FAILED unexpected worker error: {exc!r}', file=sys.stderr, flush=True)
                ok = False
            succeeded += bool(ok)
            failed += not ok
    artifacts = list(inbox.rglob('*.json'))
    if artifacts:
        changed, processed = persist(ROOT, inbox)
        print(f'artifacts={processed} changed_records={changed}', flush=True)
        for p in inbox.iterdir():
            if p.suffix in ('.json', '.skip'):
                p.unlink()
    else:
        for p in inbox.glob('*.skip'):
            p.unlink()
    print(f'summary planned={len(work)} collected={succeeded} failed={failed} planning_errors={discovery_errors}', flush=True)
    print('Changes are local. Review and commit/push them yourself.', flush=True)
    return 1 if failed or discovery_errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
