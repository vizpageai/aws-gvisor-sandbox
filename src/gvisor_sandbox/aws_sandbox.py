from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Mapping, Sequence

from kubernetes.stream import stream

from .client import GvisorSandbox
from .types import EBSBlockStorage, RunResult, S3ObjectStorage, SandboxStatus, SchedulingError


class AwsSandbox:
    """Lifecycle-oriented AWS sandbox built on EKS, gVisor, S3, and EBS.

    This is an AWS implementation of the same operational shape as managed
    agent sandboxes: named isolated environments, explicit start/stop/delete,
    persistent object storage, and persistent block storage.

    Stop/resume preserves EBS and S3 state, but it does not preserve process
    memory. That requires a VM snapshot/control-plane feature outside standard
    Kubernetes and gVisor.
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        namespace: str = "default",
        image: str = "python:3.12-slim",
        runtime_class: str | None = "gvisor",
        node_selector: Mapping[str, str] | None = None,
        service_account_name: str | None = "gvisor-sandbox",
        kube_context: str | None = None,
        s3: S3ObjectStorage | None = None,
        ebs: EBSBlockStorage | None = None,
        env: Mapping[str, str] | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name or f"aws-sandbox-{uuid.uuid4().hex[:10]}"
        self.namespace = namespace
        self.image = image
        self.runtime_class = runtime_class
        self.node_selector = dict(node_selector or {"runtime.gvisor.dev/enabled": "true"})
        self.service_account_name = service_account_name
        self.s3 = s3
        self.ebs = ebs
        self.env = dict(env or {})
        self.labels = dict(labels or {})
        self._client = GvisorSandbox(
            namespace=namespace,
            runtime_class=runtime_class,
            node_selector=self.node_selector,
            kube_context=kube_context,
            service_account_name=service_account_name,
            cleanup=False,
        )

    def start(self, *, timeout_seconds: int = 300) -> SandboxStatus:
        """Create or resume the sandbox pod, preserving any existing PVC."""

        api = self._client._api()
        if self.ebs is not None:
            self._ensure_pvc(api)

        try:
            api.create_namespaced_pod(namespace=self.namespace, body=self._build_pod())
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

        self._wait_until_ready(api, timeout_seconds=timeout_seconds)
        return self.status()

    def stop(self) -> None:
        """Stop sandbox compute. S3 objects and EBS PVC data are retained."""

        self._client.delete_pod(self.name, ignore_not_found=True)

    def delete(self, *, delete_storage: bool = False) -> None:
        """Delete sandbox compute, and optionally delete the EBS PVC."""

        api = self._client._api()
        self.stop()
        if delete_storage and self.ebs is not None:
            try:
                api.delete_namespaced_persistent_volume_claim(
                    name=self._pvc_name,
                    namespace=self.namespace,
                )
            except Exception as exc:
                if not self._client._is_not_found(exc):
                    raise

    def status(self) -> SandboxStatus:
        api = self._client._api()
        pod = api.read_namespaced_pod(name=self.name, namespace=self.namespace)
        return SandboxStatus(
            name=self.name,
            namespace=self.namespace,
            phase=pod.status.phase or "Unknown",
            ready=self._is_ready(pod),
            node_name=pod.spec.node_name,
            runtime_class_name=pod.spec.runtime_class_name,
            image=self.image,
            s3=self.s3,
            ebs=self.ebs,
        )

    def exec(self, command: Sequence[str], *, timeout_seconds: int = 300) -> str:
        """Run a command inside the sandbox container and return combined output."""

        self._wait_until_ready(self._client._api(), timeout_seconds=timeout_seconds)
        return stream(
            self._client._api().connect_get_namespaced_pod_exec,
            self.name,
            self.namespace,
            container="sandbox",
            command=list(command),
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )

    def run_python(self, code: str, *, timeout_seconds: int = 300) -> RunResult:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        output = self.exec(
            [
                "python",
                "-c",
                f"import base64; exec(base64.b64decode('{encoded}').decode('utf-8'))",
            ],
            timeout_seconds=timeout_seconds,
        )
        status = self.status()
        return RunResult(
            name=self.name,
            namespace=self.namespace,
            phase=status.phase,
            exit_code=0,
            logs=output,
            node_name=status.node_name,
            runtime_class_name=status.runtime_class_name,
            image=self.image,
            gpu=None,
        )

    @property
    def _pvc_name(self) -> str:
        if self.ebs is None:
            raise ValueError("Sandbox has no EBS block storage configured")
        return self.ebs.pvc_name or f"{self.name}-workspace"

    def _build_pod(self):
        from kubernetes import client

        labels = {
            "app": "gvisor-sandbox",
            "gvisor-sandbox/type": "aws-session",
            "gvisor-sandbox/name": self.name,
        }
        labels.update(self.labels)

        env = dict(self.env)
        if self.s3 is not None:
            env.setdefault("SANDBOX_S3_BUCKET", self.s3.bucket)
            env.setdefault("SANDBOX_S3_PREFIX", self.s3.prefix)
            env.setdefault("SANDBOX_S3_URI", self.s3.uri)
            if self.s3.region:
                env.setdefault("AWS_REGION", self.s3.region)
                env.setdefault("AWS_DEFAULT_REGION", self.s3.region)
        if self.ebs is not None:
            env.setdefault("SANDBOX_WORKSPACE", self.ebs.mount_path)

        volume_mounts = []
        volumes = []
        if self.ebs is not None:
            volume_mounts.append(client.V1VolumeMount(name="workspace", mount_path=self.ebs.mount_path))
            volumes.append(
                client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=self._pvc_name),
                )
            )

        container = client.V1Container(
            name="sandbox",
            image=self.image,
            command=["sh", "-lc", "trap 'exit 0' TERM INT; while true; do sleep 3600; done"],
            env=[client.V1EnvVar(name=key, value=value) for key, value in sorted(env.items())],
            volume_mounts=volume_mounts or None,
            security_context=client.V1SecurityContext(
                allow_privilege_escalation=False,
                capabilities=client.V1Capabilities(drop=["ALL"]),
            ),
        )

        spec = client.V1PodSpec(
            automount_service_account_token=False,
            enable_service_links=False,
            restart_policy="Never",
            runtime_class_name=self.runtime_class,
            node_selector=self.node_selector or None,
            service_account_name=self.service_account_name,
            containers=[container],
            volumes=volumes or None,
            security_context=client.V1PodSecurityContext(seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")),
            termination_grace_period_seconds=10,
        )
        metadata = client.V1ObjectMeta(name=self.name, namespace=self.namespace, labels=labels)
        return client.V1Pod(api_version="v1", kind="Pod", metadata=metadata, spec=spec)

    def _ensure_pvc(self, api) -> None:
        from kubernetes import client

        assert self.ebs is not None
        pvc = client.V1PersistentVolumeClaim(
            api_version="v1",
            kind="PersistentVolumeClaim",
            metadata=client.V1ObjectMeta(
                name=self._pvc_name,
                namespace=self.namespace,
                labels={
                    "app": "gvisor-sandbox",
                    "gvisor-sandbox/type": "aws-session-storage",
                    "gvisor-sandbox/name": self.name,
                },
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=self.ebs.storage_class_name,
                resources=client.V1VolumeResourceRequirements(
                    requests={"storage": f"{self.ebs.size_gib}Gi"},
                ),
            ),
        )
        try:
            api.create_namespaced_persistent_volume_claim(namespace=self.namespace, body=pvc)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

    def _wait_until_ready(self, api, *, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_unschedulable: str | None = None
        while time.monotonic() < deadline:
            pod = api.read_namespaced_pod(name=self.name, namespace=self.namespace)
            if self._is_ready(pod):
                return
            if pod.status.phase == "Pending":
                last_unschedulable = self._client._unschedulable_message(api, self.name) or last_unschedulable
            time.sleep(1)

        message = f"Sandbox {self.name} did not become ready within {timeout_seconds}s"
        if last_unschedulable:
            raise SchedulingError(f"{message}; last scheduling error: {last_unschedulable}")
        raise TimeoutError(message)

    def _is_ready(self, pod) -> bool:
        if pod.status.phase != "Running":
            return False
        for condition in pod.status.conditions or []:
            if condition.type == "Ready" and condition.status == "True":
                return True
        return False

    def _is_already_exists(self, exc: Exception) -> bool:
        return getattr(exc, "status", None) == 409
