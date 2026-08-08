from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gvisor_sandbox import (
    GPU,
    EBSBlockStorage,
    RuntimeMode,
    S3ObjectStorage,
    SandboxPlatform,
    SandboxSpec,
    UnsupportedConfiguration,
)
from gvisor_sandbox.cli import _key_values
from gvisor_sandbox.platform import SandboxHandle


class PlatformResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = SandboxPlatform(namespace="sandboxes")

    def test_cpu_auto_runtime_uses_gvisor(self) -> None:
        deployment = self.platform._deployment("cpu-agent", SandboxSpec(), replicas=1)
        pod_spec = deployment.spec.template.spec
        self.assertEqual(pod_spec.runtime_class_name, "gvisor")
        self.assertEqual(pod_spec.restart_policy, "Always")
        self.assertEqual(pod_spec.node_selector, {"runtime.gvisor.dev/enabled": "true"})
        self.assertEqual(pod_spec.containers[0].resources.limits["cpu"], "2")
        self.assertEqual(pod_spec.containers[0].resources.limits["memory"], "2Gi")
        self.assertEqual(pod_spec.containers[0].resources.limits["ephemeral-storage"], "4Gi")
        self.assertFalse(pod_spec.automount_service_account_token)
        self.assertFalse(pod_spec.enable_service_links)
        self.assertEqual(pod_spec.termination_grace_period_seconds, 10)

    def test_job_has_server_side_deadline(self) -> None:
        job = self.platform._job(
            "bounded-job",
            SandboxSpec(),
            ["python", "-c", "print('ok')"],
            3600,
            active_deadline_seconds=300,
        )
        self.assertEqual(job.spec.active_deadline_seconds, 300)
        self.assertEqual(job.spec.ttl_seconds_after_finished, 3600)

    def test_gpu_auto_runtime_uses_native_nvidia_resource(self) -> None:
        spec = SandboxSpec(gpu=GPU.from_type("nvidia-l4"))
        deployment = self.platform._deployment("gpu-agent", spec, replicas=1)
        pod_spec = deployment.spec.template.spec
        resources = pod_spec.containers[0].resources
        self.assertIsNone(pod_spec.runtime_class_name)
        self.assertEqual(pod_spec.node_selector["accelerator"], "nvidia-l4")
        self.assertEqual(resources.requests["nvidia.com/gpu"], "1")
        self.assertEqual(resources.limits["nvidia.com/gpu"], "1")

    def test_gpu_with_gvisor_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedConfiguration):
            SandboxSpec(runtime=RuntimeMode.GVISOR, gpu=GPU.from_type("nvidia-l4"))

    def test_s3_and_ebs_are_connected_to_container(self) -> None:
        spec = SandboxSpec(
            s3=S3ObjectStorage.from_uri("s3://agent-data/team-a", region="us-east-1"),
            ebs=EBSBlockStorage(size_gib=20, mount_path="/work"),
        )
        deployment = self.platform._deployment("stored-agent", spec, replicas=1)
        container = deployment.spec.template.spec.containers[0]
        env = {item.name: item.value for item in container.env}
        self.assertEqual(env["SANDBOX_S3_URI"], "s3://agent-data/team-a")
        self.assertEqual(env["SANDBOX_WORKSPACE"], "/work")
        self.assertEqual(container.volume_mounts[0].mount_path, "/work")

    def test_spec_annotation_round_trips(self) -> None:
        original = SandboxSpec(
            runtime="native",
            gpu=GPU.from_type("nvidia-tesla-t4"),
            s3=S3ObjectStorage.from_uri("s3://bucket/prefix"),
            ebs=EBSBlockStorage(size_gib=12),
            env={"AGENT": "1"},
        )
        restored = self.platform._stored_spec(self.platform._annotations(original))
        self.assertEqual(restored, original)

    def test_reserved_labels_cannot_be_overridden(self) -> None:
        labels = self.platform._labels("safe", "sandbox", {"sandbox.platform/name": "bad"})
        self.assertEqual(labels["sandbox.platform/name"], "safe")

    def test_service_account_defaults_only_for_s3(self) -> None:
        self.assertIsNone(self.platform._service_account(SandboxSpec()))
        s3_spec = SandboxSpec(s3=S3ObjectStorage.from_uri("s3://agent-data"))
        self.assertEqual(self.platform._service_account(s3_spec), "gvisor-sandbox")

    def test_bytes_logs_are_decoded(self) -> None:
        self.assertEqual(self.platform._normalize_text(b"job output\n"), "job output\n")
        self.assertEqual(self.platform._normalize_text("b'job output\\n'"), "job output\n")


class AgentHandleTests(unittest.TestCase):
    def test_run_returns_output_and_exit_code(self) -> None:
        handle = SandboxHandle(SimpleNamespace(), "agent")
        with (
            patch("gvisor_sandbox.platform.uuid.uuid4", return_value=SimpleNamespace(hex="abc")),
            patch.object(handle, "exec", return_value="hello\n__SANDBOX_EXIT_abc__7\n"),
        ):
            result = handle.run(["sh", "-c", "exit 7"])
        self.assertEqual(result.output, "hello")
        self.assertEqual(result.exit_code, 7)
        self.assertFalse(result.ok)


class CliParsingTests(unittest.TestCase):
    def test_key_values(self) -> None:
        self.assertEqual(_key_values(["A=1", "B=two=parts"]), {"A": "1", "B": "two=parts"})

    def test_invalid_key_value(self) -> None:
        with self.assertRaises(ValueError):
            _key_values(["missing-value"])


if __name__ == "__main__":
    unittest.main()
