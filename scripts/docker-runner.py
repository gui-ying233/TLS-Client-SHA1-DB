"""Run a bounded, resumable subset of native probes without GitHub Actions.

The repository, observations and evidence schemas are unmodified. No Git push,
commits, synthetic fingerprint data, or changes to old observations occur here.
"""
from __future__ import annotations

import argparse
import fcntl
from functools import cache
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from contextlib import nullcontext
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from typing import ContextManager

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpdb.planner import completed_case_ids, load_config, plan
from fpdb.persistence import persist
from fpdb.core import json_object
from fpdb.schema import Case, Target
STATE = ROOT / '.fpdb-local'
NODE_BASE = Path('/cache/node')
_LOCKS: dict[str, Lock] = {}
_LOCKS_GUARD = Lock()


def resource_lock(key: str) -> Lock:
    # The lock table itself is protected: two workers must get the same lock.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, Lock())


def probe_lock(target: Target, case: Case) -> ContextManager[object]:
    # npm --prefix overwrites a shared node_modules tree; hold the lock through
    # both installation AND the TLS probe so package versions cannot change.
    prepare = target['adapter'].get('prepare', [])
    if '--prefix' in prepare:
        index = prepare.index('--prefix') + 1
        if index < len(prepare):
            return resource_lock('npm:' + prepare[index])
    # These adapters build into shared paths. Keep the selected compiler's
    # output in place until all of that case's handshakes have completed.
    if target['id'] in ('java-httpclient', 'java-urlconnection'):
        return resource_lock('java:stdlib')
    if target['id'] in ('go-http', 'dotnet-httpclient', 'c-libcurl', 'cpp-boost-beast'):
        return resource_lock('build:' + target['id'])
    if target['language'] == 'rust':
        return resource_lock('cargo:' + case['runtime_version'] + ':' + target['client'])
    if target['language'] == 'ruby':
        return resource_lock('gems:' + case['runtime_version'] + ':' + target['client'])
    return nullcontext()


def run(args: list[str], *, env: dict[str, str] | None = None) -> None:
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


_FAILED_PYTHONS: dict[str, str] = {}


@cache
def python_binary(version: str) -> Path:
    if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', version):
        raise ValueError('Invalid Python version')
    if version in _FAILED_PYTHONS:
        raise ValueError(f'Python {version} previously failed in this run: {_FAILED_PYTHONS[version]}')
    try:
        run(['uv', 'python', 'install', '--no-config', version])
    except (OSError, subprocess.CalledProcessError) as exc:
        # Within one invocation failures are not repeatedly downloaded.
        # The negative cache is not persisted: transient failures are retried
        # next time. The case never gets a synthetic success record.
        _FAILED_PYTHONS[version] = str(exc)
        raise
    path = subprocess.check_output(
        ['uv', 'python', 'find', '--no-config', '--managed-python', version], text=True).strip()
    if not path:
        raise ValueError('UV did not provide a Python interpreter')
    actual = subprocess.check_output(
        [path, '-c', 'import platform; print(platform.python_version())'], text=True).strip()
    if not (actual == version or (version.count('.') == 1 and actual.startswith(version + '.'))):
        raise ValueError(f'Python runtime {actual} != {version}')
    return Path(path)


def supported(target: Target) -> bool:
    if target.get('runner') != 'ubuntu-24.04' or target.get('platform') != 'linux-x86_64':
        return False
    return not target['id'].startswith('env-') and target['language'] in {
        'node', 'python', 'go', 'java', 'php', 'ruby', 'rust', 'dotnet',
        'bun', 'deno', 'dart', 'erlang', 'perl', 'c', 'cpp', 'ubuntu'}


@cache
def managed_environment(language: str, version: str) -> dict[str, str]:
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}|stable', version):
        raise ValueError('Invalid runtime version')
    requested = 'latest' if version == 'stable' else version
    if language == 'java':
        requested = 'temurin-' + requested
    tool = language + '@' + requested
    if language == 'rust':
        tool = 'rust[profile=minimal]@' + requested
    # Tools are explicit argv values; no project configuration or shell eval.
    installed = subprocess.run(['mise', 'install', tool], cwd='/tmp',
                               capture_output=True, text=True, check=False)
    if installed.returncode:
        raise ValueError(f'Could not install {tool}: {installed.stderr[-2500:]}')
    process = subprocess.run(['mise', 'env', '--json', tool], cwd='/tmp',
                             capture_output=True, text=True, check=False)
    if process.returncode:
        raise ValueError(f'Could not install {tool}: {process.stderr[-2500:]}')
    values = json_object(json.loads(process.stdout))
    result: dict[str, str] = {}
    for key in ('PATH', 'JAVA_HOME', 'GOROOT', 'GOPATH', 'DOTNET_ROOT', 'DOTNET_MULTILEVEL_LOOKUP',
                'RUSTUP_HOME', 'CARGO_HOME', 'RUSTUP_TOOLCHAIN', 'LD_LIBRARY_PATH'):
        if key in values:
            value = values[key]
            if not isinstance(value, str):
                raise ValueError(f'Runtime environment {key} must be a string')
            result[key] = value
    if 'PATH' not in result:
        raise ValueError(f'No executable path for {tool}')
    return result


def runtime_environment(case: Case) -> dict[str, str]:
    language, version = case['language'], case['runtime_version']
    env = os.environ.copy()
    if language == 'node':
        with resource_lock('node:' + version):
            node = node_binary(version)
        env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    elif language == 'python':
        with resource_lock('python:' + version):
            env['FPDB_PYTHON_BIN'] = str(python_binary(version))
    elif language in {'perl', 'c', 'cpp', 'ubuntu'}:
        return env
    else:
        # Reuse matching distribution runtimes already supplied by the image.
        if language == 'php' and re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', version):
            php = Path('/usr/bin/php' + '.'.join(version.split('.')[:2]))
            actual = subprocess.check_output([str(php), '-r', 'echo PHP_VERSION;'], text=True) if php.is_file() else ''
            if actual == version or actual.startswith(version + '.'):
                with resource_lock('php:' + version):
                    directory = Path('/cache/php') / version / 'bin'
                    directory.mkdir(parents=True, exist_ok=True)
                    # Composer's distro shebang otherwise selects the system PHP.
                    for name, extra in (('php', ''), ('composer', ' /usr/bin/composer')):
                        wrapper = directory / name
                        content = f'#!/bin/sh\nexec {php}{extra} "$@"\n'
                        if not wrapper.exists() or wrapper.read_text(encoding='utf-8') != content:
                            temporary = wrapper.with_suffix('.tmp')
                            temporary.write_text(content, encoding='utf-8')
                            temporary.chmod(0o755)
                            temporary.replace(wrapper)
                env['PATH'] = str(directory) + os.pathsep + env.get('PATH', '')
                return env
        if language == 'ruby' and shutil.which('ruby'):
            actual = subprocess.check_output(['ruby', '-e', 'print RUBY_VERSION'], text=True)
            if actual == version or actual.startswith(version + '.'):
                return ruby_environment(case, env)
        # The existing shared .NET cache may already contain a newer SDK plus
        # the requested runtime. Never require another SDK download in that case.
        dotnet = Path('/cache/dotnet/dotnet')
        if language == 'dotnet' and dotnet.is_file():
            runtimes = subprocess.check_output([str(dotnet), '--list-runtimes'], text=True)
            sdks = subprocess.check_output([str(dotnet), '--list-sdks'], text=True)
            if (any(line.startswith('Microsoft.NETCore.App ' + version + '.') for line in runtimes.splitlines())
                    and any(int(line.split('.')[0]) >= int(version.split('.')[0]) for line in sdks.splitlines())):
                env.update(DOTNET_ROOT='/cache/dotnet', DOTNET_MULTILEVEL_LOOKUP='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
                env['PATH'] = '/cache/dotnet' + os.pathsep + env.get('PATH', '')
                return env
        with resource_lock('runtime:' + language):
            env.update(managed_environment(language, version))
        if language == 'ruby':
            return ruby_environment(case, env)
        if language == 'rust':
            env['FPDB_RUST_TOOLCHAIN'] = env['RUSTUP_TOOLCHAIN']
            env['CARGO_TARGET_DIR'] = str(Path('/cache/cargo-target') / version / case['client'])
            env['CARGO_BUILD_JOBS'] = '2'
        if language == 'go':
            env['GOCACHE'] = '/cache/go-build'
            env['GOMODCACHE'] = '/cache/go-mod'
    return env


def ruby_environment(case: Case, env: dict[str, str]) -> dict[str, str]:
    home = Path('/cache/gems') / case['runtime_version'] / case['client'] / case['client_version']
    home.mkdir(parents=True, exist_ok=True)
    env.update(GEM_HOME=str(home), GEM_PATH=str(home))
    return env


def collect_one(case: Case, target: Target, inbox: Path) -> bool:
    if not supported(target):
        raise ValueError('Unsupported local Docker target')
    env = runtime_environment(case)
    # Explicit case identity is always verified again by fpdb.collector.
    env['FPDB_CASES_JSON'] = json.dumps([case], separators=(',', ':'))
    with probe_lock(target, case):
        code = subprocess.run([sys.executable, '-m', 'fpdb.cli', 'collect',
                               '--cases-env', 'FPDB_CASES_JSON', '--output-dir', str(inbox)],
                              cwd=ROOT, env=env, check=False).returncode
    if code:
        print(f'FAILED case={case["id"]} target={target["id"]}', flush=True)
    return code == 0 and (inbox / (case['id'] + '.json')).is_file()


class Arguments(argparse.Namespace):
    target: str | None
    all_supported: bool
    mode: str
    jobs: int
    max_cases: int
    parallel: int
    plan_parallel: int
    recheck: bool
    list_targets: bool
    reindex: bool


def main() -> int:
    parser = argparse.ArgumentParser(description='Docker-hosted TLS fingerprint collector')
    parser.add_argument('--target', help='Existing target id')
    parser.add_argument('--all-supported', action='store_true', help='Round-robin supported targets')
    parser.add_argument('--mode', choices=('incremental', 'historical', 'full'), default='incremental')
    parser.add_argument('--jobs', type=int, default=4, help='Planner batch budget (1..200)')
    parser.add_argument('--max-cases', type=int, default=32, help='Maximum cases per local invocation')
    parser.add_argument('--parallel', type=int, default=1, help='Concurrent Case workers (1..16; default 1)')
    parser.add_argument('--plan-parallel', type=int, default=32,
                        help='Concurrent target planners (1..64; per-host network cap is separate)')
    parser.add_argument('--recheck', action='store_true', help='Explicitly repeat already collected cases')
    parser.add_argument('--list-targets', action='store_true')
    parser.add_argument('--reindex', action='store_true', help='Rebuild index from remaining observations')
    args = parser.parse_args(namespace=Arguments())
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
    if not 1 <= args.plan_parallel <= 64:
        parser.error('--plan-parallel must be 1..64')
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
    # The snapshot is stable until all workers finish and persist() runs.
    scan_start = time.perf_counter()
    if not args.recheck:
        print('scanning completed Case IDs...', flush=True)
    completed = set() if args.recheck else completed_case_ids(ROOT)
    print(f'completed_case_ids={len(completed)} scan_seconds={time.perf_counter() - scan_start:.2f}', flush=True)
    discovery_errors = 0
    planned = 0
    succeeded, failed = 0, 0
    print(f'planning targets={len(selected)} planners={args.plan_parallel} '
          f'network_per_host={os.environ.get("FPDB_VERSION_PER_HOST", "4")}', flush=True)

    def plan_target(target: Target) -> list[Case]:
        return plan(ROOT, mode=args.mode, target_filter=target['id'],
                    recheck=args.recheck, batch_size=args.jobs,
                    completed_ids=completed, config=config)

    def worker(i: int, target: Target, case: Case) -> bool:
        verification = 'verify=false' if case.get('verify') is False else 'default'
        print(f'[{i}] {target["id"]} {case["runtime_version"]} / {case["client_version"]} {verification}', flush=True)
        try:
            ok = collect_one(case, target, inbox)
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            print(f'FAILED setup case={case["id"]}: {exc}', file=sys.stderr, flush=True)
            ok = False
        print(f'[{i}] {"OK" if ok else "FAILED"} case={case["id"]}', flush=True)
        return ok

    def record_result(future: Future[bool]) -> None:
        nonlocal succeeded, failed
        try:
            ok = future.result()
        except Exception as exc:
            # Do not lose other successful artifacts due to one unexpected error.
            print(f'FAILED unexpected worker error: {exc!r}', file=sys.stderr, flush=True)
            ok = False
        succeeded += bool(ok)
        failed += not ok

    # Planning runs ahead concurrently. We only wait for each target when its
    # turn arrives, preserving the original deterministic round-robin order.
    # Collected cases execute immediately; no full-matrix planning barrier.
    with ThreadPoolExecutor(max_workers=args.plan_parallel) as planners, \
         ThreadPoolExecutor(max_workers=args.parallel) as pool:
        planned_futures = [(t, planners.submit(plan_target, t)) for t in selected]
        queues: list[tuple[Target, Future[list[Case]], Iterator[Case] | None]] = [
            (t, f, None) for t, f in planned_futures
        ]
        examined: set[Future[list[Case]]] = set()
        inflight: set[Future[bool]] = set()
        max_inflight = args.parallel * 2

        def planning_error(target: Target, exc: Exception) -> None:
            nonlocal discovery_errors
            discovery_errors += 1
            print(f'FAILED planning target={target["id"]}: {exc}', file=sys.stderr, flush=True)

        while planned < args.max_cases and queues:
            next_queues: list[tuple[Target, Future[list[Case]], Iterator[Case] | None]] = []
            for target, future, pending in queues:
                if pending is None:
                    examined.add(future)
                    try:
                        pending = iter(future.result())
                    except (OSError, ValueError, KeyError) as exc:
                        planning_error(target, exc)
                        continue
                try:
                    case = next(pending)
                except StopIteration:
                    continue
                planned += 1
                inflight.add(pool.submit(worker, planned, target, case))
                next_queues.append((target, future, pending))
                if len(inflight) >= max_inflight:
                    done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for finished in done:
                        inflight.remove(finished)
                        record_result(finished)
                if planned >= args.max_cases:
                    break
            queues = next_queues

        # Like the old runner, report errors from every selected target, even
        # when --max-cases was reached before its queue was needed.
        for target, future in planned_futures:
            if future not in examined:
                try:
                    future.result()
                except (OSError, ValueError, KeyError) as exc:
                    planning_error(target, exc)
        print(f'planned_cases={planned} targets={len(selected)} mode={args.mode} '
              f'parallel={args.parallel}', flush=True)
        for future in as_completed(inflight):
            record_result(future)
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
    print(f'summary planned={planned} collected={succeeded} failed={failed} planning_errors={discovery_errors}', flush=True)
    print('Changes are local. Review and commit/push them yourself.', flush=True)
    return 1 if failed or discovery_errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
