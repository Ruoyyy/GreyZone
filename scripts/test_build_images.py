"""Offline regression checks for enforced image-build resource limits."""

from contextlib import nullcontext
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import build_images as build


class BuildMemoryTests(unittest.TestCase):
    def test_concurrent_invocations_are_refused(self):
        with tempfile.TemporaryDirectory() as cache, patch.dict('os.environ', {'XDG_CACHE_HOME': cache}):
            with build.build_lock():
                with self.assertRaisesRegex(ValueError, 'Another GreyZone image build'):
                    with build.build_lock():
                        self.fail('Concurrent builders must not share a memory budget.')

    def test_every_tracked_dockerfile_has_a_budget(self):
        files = subprocess.check_output(['git', 'ls-files', '*Dockerfile*'], cwd=build.ROOT, text=True).splitlines()
        self.assertEqual(set(files), set(build.POLICY['dockerfiles_mib']))

    def test_unknown_dockerfile_requires_an_explicit_budget(self):
        with self.assertRaisesRegex(ValueError, 'No build memory budget'):
            build.memory_limit(build.ROOT / '.deployment-checks/Dockerfile')
        self.assertEqual(build.memory_limit(build.ROOT / '.deployment-checks/Dockerfile', 512), 512)
        with self.assertRaisesRegex(ValueError, 'at least 256'):
            build.memory_limit(build.ROOT / 'Clawith/backend/Dockerfile', 0)

    def test_runtime_memory_and_swap_must_both_match(self):
        expected = 512 * 1024 * 1024
        build.verify_builder({'HostConfig': {'Memory': expected, 'MemorySwap': expected}}, 512)
        for limits in [{'Memory': 0, 'MemorySwap': 0}, {'Memory': expected, 'MemorySwap': -1}]:
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, 'refusing an unrestricted build'):
                build.verify_builder({'HostConfig': limits}, 512)

    def test_buildx_normalized_configuration_retains_serial_workers(self):
        normalized = '[worker]\n[worker.containerd]\nmax-parallelism = 1\n\n[worker.oci]\nmax-parallelism = 1\n'
        build.verify_parallelism(normalized)
        for invalid in [normalized.replace('= 1', '= 4'), '[worker.oci]\nmax-parallelism = 1\n']:
            with self.assertRaisesRegex(ValueError, 'concurrency to one'):
                build.verify_parallelism(invalid)

    def test_unapplied_limit_stops_builder_without_running_a_build(self):
        def fake_docker(*args, **kwargs):
            if args[0] == 'info':
                return SimpleNamespace(stdout=str(8 * 1024**3), returncode=0)
            if args[0] == 'inspect':
                return SimpleNamespace(stdout=json.dumps([{'HostConfig': {'Memory': 0, 'MemorySwap': 0}}]), returncode=0)
            return SimpleNamespace(stdout='', returncode=0)

        with patch.object(build, 'docker', side_effect=fake_docker) as docker, \
             patch.object(build, 'available_memory_mib', return_value=4096):
            with self.assertRaisesRegex(ValueError, 'refusing an unrestricted build'):
                with build.bounded_builder(512):
                    self.fail('An unrestricted builder must never execute a build.')
            self.assertIn(('buildx', 'stop', 'greyzone-build-512m'), [call.args for call in docker.call_args_list])

    def test_insufficient_docker_memory_does_not_create_builder(self):
        with patch.object(build, 'docker', return_value=SimpleNamespace(stdout=str(512 * 1024**2))) as docker:
            with self.assertRaisesRegex(ValueError, '512 MiB reserve'):
                with build.bounded_builder(1024):
                    self.fail('Insufficient Docker memory must fail before bootstrap.')
            self.assertEqual(docker.call_count, 1)

    def test_busy_host_is_refused_even_when_docker_total_is_large(self):
        with self.assertRaisesRegex(ValueError, 'currently available locally'):
            build.verify_capacity(4096, 16 * 1024**3, 2048)
        build.verify_capacity(512, 8 * 1024**3, 2048)

    def test_dry_run_never_starts_builder(self):
        with patch.object(build, 'bounded_builder') as builder, patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(build.main([
                '--dockerfile', 'Clawith/backend/Dockerfile', '--context', 'Clawith/backend',
                '--tag', 'greyzone-test:local', '--dry-run',
            ]), 0)
            builder.assert_not_called()
            self.assertIn('2048 MiB', output.getvalue())

    def test_quiet_mode_returns_only_the_image_id(self):
        with patch.object(build, 'build_lock', return_value=nullcontext()), \
             patch.object(build, 'bounded_builder', return_value=nullcontext('test-builder')), \
             patch.object(build, 'docker', return_value=SimpleNamespace(stdout='sha256:fixture\n')) as docker, \
             patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(build.main([
                '--dockerfile', 'Clawith/backend/Dockerfile', '--context', 'Clawith/backend',
                '--tag', 'greyzone-test:local', '--quiet',
            ]), 0)
            self.assertEqual(output.getvalue(), 'sha256:fixture\n')
            self.assertIn('test-builder', docker.call_args.args)


if __name__ == '__main__':
    unittest.main()
