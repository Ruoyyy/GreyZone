#!/usr/bin/env python3
"""Build benchmark images serially in a memory-limited BuildKit container."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'scripts/build-memory.json').read_text())
BUILDKIT_IMAGE = 'moby/buildkit:v0.33.0@sha256:6c2fa84a6b61ccd72899dde4239f8d5717f05f9a8ca6f3cad185fb1a95a94de3'


def docker(*args, capture=False, check=True):
    return subprocess.run(
        ['docker', *map(str, args)], check=check, text=True,
        stdout=subprocess.PIPE if capture else sys.stderr,
        stderr=subprocess.PIPE if capture else None,
    )


def memory_limit(dockerfile, override=None):
    try:
        key = str(Path(dockerfile).resolve().relative_to(ROOT))
        value = override if override is not None else POLICY['dockerfiles_mib'][key]
    except (ValueError, KeyError) as exc:
        raise ValueError(f'No build memory budget for {dockerfile}. Update scripts/build-memory.json.') from exc
    if not isinstance(value, int) or value < 256:
        raise ValueError('Build memory must be at least 256 MiB.')
    return value


@contextmanager
def build_lock():
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'greyzone-build'
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / 'build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another GreyZone image build is running. Build one range at a time.') from exc
        yield


def verify_builder(config, memory_mib):
    limits = config['HostConfig']
    expected = memory_mib * 1024 * 1024
    if limits.get('Memory') != expected or limits.get('MemorySwap') != expected:
        raise ValueError('BuildKit memory/swap limits were not applied; refusing an unrestricted build.')


def verify_parallelism(config):
    # Buildx normalizes TOML formatting while copying the configuration.
    for worker in ('oci', 'containerd'):
        section = re.search(rf'(?ms)^[ \t]*\[worker\.{worker}\][ \t]*\n(.*?)(?=^[ \t]*\[|\Z)', config)
        if not section or not re.search(r'(?m)^[ \t]*max-parallelism[ \t]*=[ \t]*1[ \t]*$', section[1]):
            raise ValueError('The builder must limit worker concurrency to one; remove it before rebuilding.')


def available_memory_mib():
    try:
        match = re.search(r'(?m)^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text())
        return int(match[1]) // 1024 if match else None
    except OSError:
        return None


def verify_capacity(memory_mib, total_bytes, available_mib):
    if (memory_mib + 512) * 1024 * 1024 > total_bytes:
        raise ValueError(f'{memory_mib} MiB exceeds Docker memory with a 512 MiB reserve. Set --memory-mib explicitly or increase Docker memory.')
    if available_mib is not None and memory_mib + 512 > available_mib:
        raise ValueError(f'Only {available_mib} MiB is currently available locally; {memory_mib} MiB plus a 512 MiB reserve is required. Stop other workloads or set --memory-mib explicitly.')


@contextmanager
def bounded_builder(memory_mib):
    total = int(docker('info', '--format', '{{.MemTotal}}', capture=True).stdout)
    verify_capacity(memory_mib, total, available_memory_mib())
    name = f'greyzone-build-{memory_mib}m'
    existing = docker('buildx', 'inspect', name, capture=True, check=False)
    if existing.returncode:
        docker(
            'buildx', 'create', '--name', name, '--driver', 'docker-container',
            '--driver-opt', f'image={BUILDKIT_IMAGE}',
            '--driver-opt', f'memory={memory_mib}m,memory-swap={memory_mib}m,default-load=true',
            '--buildkitd-config', ROOT / 'scripts/buildkitd.toml',
        )
    try:
        docker('buildx', 'inspect', name, '--bootstrap')
        config = json.loads(docker('inspect', f'buildx_buildkit_{name}0', capture=True).stdout)[0]
        verify_builder(config, memory_mib)
        if config['Config']['Image'] != BUILDKIT_IMAGE:
            raise ValueError('An existing builder uses a different BuildKit image; remove it before rebuilding.')
        installed = docker('exec', f'buildx_buildkit_{name}0', 'cat', '/etc/buildkit/buildkitd.toml', capture=True).stdout
        verify_parallelism(installed)
        yield name
    finally:
        docker('buildx', 'stop', name, check=False)


def compose_jobs(args):
    configured = args.compose_file or POLICY['compose_files'][args.project]
    if not configured:
        raise ValueError('This range uses standalone Dockerfiles. Use --dockerfile, --context and --tag.')
    compose_file = (ROOT / configured).resolve()
    prefix = ['compose', '--parallel', '1', '-f', str(compose_file)]
    for profile in args.profile:
        prefix += ['--profile', profile]
    config_options = ['--no-interpolate', '--no-env-resolution'] if args.dry_run else []
    config = json.loads(docker(*prefix, '--profile', '*', 'config', *config_options, '--format', 'json', capture=True).stdout)
    services = config.get('services', {})
    selected = set(args.service)
    unknown = selected - services.keys()
    if unknown:
        raise ValueError(f'Unknown service(s): {", ".join(sorted(unknown))}')
    profiles = set(args.profile) | set(filter(None, os.environ.get('COMPOSE_PROFILES', '').split(',')))
    jobs = []
    for service, spec in services.items():
        if selected and service not in selected:
            continue
        if not selected and spec.get('profiles') and '*' not in profiles and not profiles.intersection(spec['profiles']):
            continue
        build = spec.get('build')
        if not build:
            continue
        if 'dockerfile_inline' in build:
            raise ValueError(f'{service}: inline Dockerfiles need an explicit budgeted Dockerfile.')
        context = Path(build['context'])
        if not context.is_absolute():
            context = compose_file.parent / context
        dockerfile = context / build.get('dockerfile', 'Dockerfile')
        memory = memory_limit(dockerfile, args.memory_mib)
        jobs.append((service, memory, prefix + ['build', '--builder', '{builder}', service]))
    return jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--project', choices=POLICY['compose_files'])
    mode.add_argument('--dockerfile', help='Dockerfile path relative to the repository root, or absolute')
    parser.add_argument('--compose-file', help='Override the project Compose file, relative to the repository root')
    parser.add_argument('--service', action='append', default=[])
    parser.add_argument('--profile', action='append', default=[])
    parser.add_argument('--context', help='Standalone build context, relative to the repository root')
    parser.add_argument('--tag', action='append', default=[])
    parser.add_argument('--build-arg', action='append', default=[])
    parser.add_argument('--platform', help='Target platform for a standalone build')
    parser.add_argument('--push', action='store_true', help='Push a standalone build instead of loading it locally')
    parser.add_argument('--memory-mib', type=int, help='Explicit memory ceiling override, in MiB')
    parser.add_argument('--quiet', action='store_true', help='Print only the image ID on successful standalone builds')
    parser.add_argument('--dry-run', action='store_true', help='List selected image budgets without starting a builder')
    args = parser.parse_args(argv)
    if args.dockerfile:
        if not args.context or not args.tag:
            parser.error('--dockerfile requires --context and --tag')
        dockerfile = (ROOT / args.dockerfile).resolve()
        context = (ROOT / args.context).resolve()
        if not dockerfile.is_file() or not context.is_dir():
            parser.error('The Dockerfile and build context must exist.')
        command = ['buildx', 'build', '--builder', '{builder}', '--push' if args.push else '--load', '--file', str(dockerfile)]
        for tag in args.tag:
            command += ['--tag', tag]
        for build_arg in args.build_arg:
            command += ['--build-arg', build_arg]
        if args.platform:
            command += ['--platform', args.platform]
        if args.quiet:
            command += ['--quiet']
        jobs = [(str(dockerfile.relative_to(ROOT)), memory_limit(dockerfile, args.memory_mib), command + [str(context)])]
    else:
        if args.context or args.tag or args.build_arg or args.platform or args.quiet or args.push:
            parser.error('--context, --tag, --build-arg, --platform, --quiet and --push require --dockerfile')
        jobs = compose_jobs(args)
    if not jobs:
        print('No local image builds selected; the selected services use prebuilt images.')
        return 0
    if args.dry_run:
        for label, memory, _ in jobs:
            print(f'{label}: {memory} MiB, swap disabled, one build step at a time')
        return 0
    with build_lock():
        for label, memory, command in jobs:
            print(f'Building {label}: {memory} MiB; swap disabled; serial build.', file=sys.stderr, flush=True)
            with bounded_builder(memory) as name:
                # Keep builder diagnostics off stdout so callers can capture --quiet image IDs.
                result = docker(*(name if part == '{builder}' else part for part in command), capture=args.quiet)
                if args.quiet:
                    print(result.stdout.strip())
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            print(f'Build failed (exit {exc.returncode}). The configured memory ceiling was retained.', file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
        else:
            print(f'Build refused: {exc}', file=sys.stderr)
        sys.exit(1)
