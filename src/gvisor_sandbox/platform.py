from __future__ import annotations

import ast
import base64
import builtins
import json
import re
import shlex
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kubernetes.stream import stream

from .types import (
    GPU,
    CommandResult,
    ComputeResources,
    EBSBlockStorage,
    RunResult,
    RuntimeMode,
    S3ObjectStorage,
    SandboxSpec,
    SandboxStatus,
    SchedulingError,
)

MANAGED_LABEL = "sandbox.platform/managed"
NAME_LABEL = "sandbox.platform/name"
KIND_LABEL = "sandbox.platform/kind"
SPEC_ANNOTATION = "sandbox.platform/spec"
EXPIRES_ANNOTATION = "sandbox.platform/expires-at"


class SandboxPlatform:
    """Kubernetes control plane for CPU/GPU sandboxes and agent jobs.

    Reusable sandboxes are Deployments that can be scaled between zero and one.
    Jobs use the Kubernetes Job controller and can run synchronously or remain
    detached for long-running work. EBS state is held in a PVC, while S3 access
    is exposed through environment metadata and the sandbox service account.
    """

    def __init__(
        self,
        *,
        namespace: str = "default",
        kube_context: str | None = None,
        cpu_node_selector: Mapping[str, str] | None = None,
        gpu_node_selector: Mapping[str, str] | None = None,
        service_account_name: str | None = None,
    ) -> None:
        self.namespace = namespace
        self.kube_context = kube_context
        self.cpu_node_selector = dict(cpu_node_selector or {"runtime.gvisor.dev/enabled": "true"})
        self.gpu_node_selector = dict(gpu_node_selector or {})
        self.service_account_name = service_account_name
        self._core = None
        self._apps = None
        self._batch = None

    def create(
        self,
        name: str,
        spec: SandboxSpec | None = None,
        *,
        start: bool = True,
        wait: bool = True,
        timeout_seconds: int = 600,
    ) -> SandboxHandle:
        """Create a reusable sandbox and optionally wait for it to be ready."""

        spec = spec or SandboxSpec()
        name = self._validate_name(name)
        core, apps, _ = self._apis()
        self._validate_service_account(spec)
        if spec.ebs is not None:
            self._ensure_pvc(name, spec.ebs)

        deployment = self._deployment(name, spec, replicas=1 if start else 0)
        try:
            apps.create_namespaced_deployment(namespace=self.namespace, body=deployment)
        except Exception as exc:
            if self._status_code(exc) == 409:
                raise ValueError(f"Sandbox {name!r} already exists") from exc
            raise

        handle = SandboxHandle(self, name)
        if start and wait:
            self._wait_for_sandbox(name, timeout_seconds)
        return handle

    def sandbox(self, name: str) -> SandboxHandle:
        """Connect to an existing named sandbox without changing it."""

        self._apps_api().read_namespaced_deployment(self._validate_name(name), self.namespace)
        return SandboxHandle(self, name)

    def list(self) -> list[SandboxStatus]:
        deployments = self._apps_api().list_namespaced_deployment(
            self.namespace,
            label_selector=f"{MANAGED_LABEL}=true,{KIND_LABEL}=sandbox",
        )
        return [self.status(item.metadata.name) for item in deployments.items]

    def status(self, name: str) -> SandboxStatus:
        name = self._validate_name(name)
        deployment = self._apps_api().read_namespaced_deployment(name, self.namespace)
        pod = self._sandbox_pod(name, require=False)
        stored_spec = self._stored_spec(deployment.metadata.annotations or {})
        stopped = (deployment.spec.replicas or 0) == 0
        return SandboxStatus(
            name=name,
            namespace=self.namespace,
            phase="Stopped" if stopped else (pod.status.phase if pod is not None else "Pending"),
            ready=False if pod is None else self._pod_ready(pod),
            node_name=None if pod is None else pod.spec.node_name,
            runtime_class_name=self._runtime_class(stored_spec),
            image=stored_spec.image,
            s3=stored_spec.s3,
            ebs=stored_spec.ebs,
            gpu=stored_spec.gpu,
            stopped=stopped,
        )

    def start(self, name: str, *, wait: bool = True, timeout_seconds: int = 600) -> SandboxStatus:
        self._scale(name, 1)
        if wait:
            self._wait_for_sandbox(name, timeout_seconds)
        return self.status(name)

    def stop(self, name: str, *, wait: bool = True, timeout_seconds: int = 300) -> SandboxStatus:
        self._scale(name, 0)
        if wait:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if self._sandbox_pod(name, require=False) is None:
                    break
                time.sleep(1)
            else:
                raise TimeoutError(f"Sandbox {name} did not stop within {timeout_seconds}s")
        return self.status(name)

    def delete(self, name: str, *, delete_storage: bool = False) -> None:
        name = self._validate_name(name)
        apps = self._apps_api()
        annotations: dict[str, str] = {}
        try:
            deployment = apps.read_namespaced_deployment(name, self.namespace)
            annotations = deployment.metadata.annotations or {}
            apps.delete_namespaced_deployment(name, self.namespace)
        except Exception as exc:
            if self._status_code(exc) != 404:
                raise

        if delete_storage:
            spec = self._stored_spec(annotations) if annotations else None
            pvc_name = self._pvc_name(name, spec.ebs) if spec and spec.ebs else f"{name}-workspace"
            try:
                self._core_api().delete_namespaced_persistent_volume_claim(pvc_name, self.namespace)
            except Exception as exc:
                if self._status_code(exc) != 404:
                    raise

    def submit_python(
        self,
        code: str,
        *,
        name: str | None = None,
        spec: SandboxSpec | None = None,
        wait: bool = True,
        timeout_seconds: int = 3600,
        ttl_seconds_after_finished: int | None = None,
    ) -> JobHandle | RunResult:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = [
            "python",
            "-c",
            f"import base64; exec(compile(base64.b64decode('{encoded}'), '<sandbox-job>', 'exec'))",
        ]
        return self.submit_command(
            command,
            name=name,
            spec=spec,
            wait=wait,
            timeout_seconds=timeout_seconds,
            ttl_seconds_after_finished=ttl_seconds_after_finished,
        )

    def submit_command(
        self,
        command: Sequence[str],
        *,
        name: str | None = None,
        spec: SandboxSpec | None = None,
        wait: bool = True,
        timeout_seconds: int = 3600,
        ttl_seconds_after_finished: int | None = None,
    ) -> JobHandle | RunResult:
        spec = spec or SandboxSpec()
        name = self._validate_name(name or f"sandbox-job-{uuid.uuid4().hex[:10]}")
        self._validate_service_account(spec)
        if spec.ebs is not None:
            self._ensure_pvc(name, spec.ebs)
        job = self._job(
            name,
            spec,
            list(command),
            ttl_seconds_after_finished,
            active_deadline_seconds=timeout_seconds,
        )
        try:
            self._batch_api().create_namespaced_job(self.namespace, job)
        except Exception as exc:
            if self._status_code(exc) == 409:
                raise ValueError(f"Job {name!r} already exists") from exc
            raise
        handle = JobHandle(self, name)
        return handle.wait(timeout_seconds=timeout_seconds) if wait else handle

    def job(self, name: str) -> JobHandle:
        self._batch_api().read_namespaced_job(self._validate_name(name), self.namespace)
        return JobHandle(self, name)

    def list_jobs(self) -> builtins.list[str]:
        jobs = self._batch_api().list_namespaced_job(
            self.namespace,
            label_selector=f"{MANAGED_LABEL}=true,{KIND_LABEL}=job",
        )
        return [item.metadata.name for item in jobs.items]

    def cleanup_expired(self, *, delete_storage: bool = False) -> builtins.list[str]:
        """Delete platform resources whose configured wall-clock TTL elapsed."""

        now = datetime.now(timezone.utc)
        removed: builtins.list[str] = []
        deployments = self._apps_api().list_namespaced_deployment(
            self.namespace, label_selector=f"{MANAGED_LABEL}=true,{KIND_LABEL}=sandbox"
        )
        for item in deployments.items:
            if self._is_expired(item.metadata.annotations or {}, now):
                self.delete(item.metadata.name, delete_storage=delete_storage)
                removed.append(item.metadata.name)

        jobs = self._batch_api().list_namespaced_job(
            self.namespace, label_selector=f"{MANAGED_LABEL}=true,{KIND_LABEL}=job"
        )
        for item in jobs.items:
            if self._is_expired(item.metadata.annotations or {}, now):
                JobHandle(self, item.metadata.name).delete(delete_storage=delete_storage)
                removed.append(item.metadata.name)
        return removed

    def _deployment(self, name: str, spec: SandboxSpec, *, replicas: int):
        from kubernetes import client

        labels = self._labels(name, "sandbox", spec.labels)
        annotations = self._annotations(spec)
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels=labels, annotations=annotations),
            spec=self._pod_spec(name, spec, restart_policy="Always"),
        )
        return client.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(name=name, namespace=self.namespace, labels=labels, annotations=annotations),
            spec=client.V1DeploymentSpec(
                replicas=replicas,
                selector=client.V1LabelSelector(match_labels={NAME_LABEL: name, KIND_LABEL: "sandbox"}),
                strategy=client.V1DeploymentStrategy(type="Recreate"),
                template=template,
            ),
        )

    def _job(
        self,
        name: str,
        spec: SandboxSpec,
        command: builtins.list[str],
        ttl_seconds_after_finished: int | None,
        *,
        active_deadline_seconds: int,
    ):
        from kubernetes import client

        labels = self._labels(name, "job", spec.labels)
        annotations = self._annotations(spec)
        return client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=name, namespace=self.namespace, labels=labels, annotations=annotations),
            spec=client.V1JobSpec(
                active_deadline_seconds=active_deadline_seconds,
                backoff_limit=0,
                ttl_seconds_after_finished=ttl_seconds_after_finished,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels, annotations=annotations),
                    spec=self._pod_spec(name, spec, command=command, restart_policy="Never"),
                ),
            ),
        )

    def _pod_spec(
        self,
        name: str,
        spec: SandboxSpec,
        *,
        command: builtins.list[str] | None = None,
        restart_policy: str = "Never",
    ):
        from kubernetes import client

        env = dict(spec.env)
        if spec.s3 is not None:
            env.setdefault("SANDBOX_S3_BUCKET", spec.s3.bucket)
            env.setdefault("SANDBOX_S3_PREFIX", spec.s3.prefix)
            env.setdefault("SANDBOX_S3_URI", spec.s3.uri)
            if spec.s3.region:
                env.setdefault("AWS_REGION", spec.s3.region)
                env.setdefault("AWS_DEFAULT_REGION", spec.s3.region)
        if spec.ebs is not None:
            env.setdefault("SANDBOX_WORKSPACE", spec.ebs.mount_path)

        requests = {"cpu": spec.resources.cpu, "memory": spec.resources.memory}
        limits: dict[str, str] = {}
        if spec.resources.cpu_limit:
            limits["cpu"] = spec.resources.cpu_limit
        if spec.resources.memory_limit:
            limits["memory"] = spec.resources.memory_limit
        if spec.resources.ephemeral_storage:
            requests["ephemeral-storage"] = spec.resources.ephemeral_storage
            limits["ephemeral-storage"] = spec.resources.ephemeral_storage
        if spec.gpu is not None:
            requests[spec.gpu.resource_name] = str(spec.gpu.count)
            limits[spec.gpu.resource_name] = str(spec.gpu.count)
            env.setdefault("SANDBOX_GPU_TYPE", spec.gpu.type)

        mounts = []
        volumes = []
        if spec.ebs is not None:
            mounts.append(client.V1VolumeMount(name="workspace", mount_path=spec.ebs.mount_path))
            volumes.append(
                client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self._pvc_name(name, spec.ebs)
                    ),
                )
            )

        container = client.V1Container(
            name="sandbox",
            image=spec.image,
            command=command or ["sh", "-lc", "trap 'exit 0' TERM INT; while true; do sleep 3600; done"],
            env=[client.V1EnvVar(name=key, value=value) for key, value in sorted(env.items())],
            resources=client.V1ResourceRequirements(requests=requests, limits=limits or None),
            volume_mounts=mounts or None,
            working_dir=spec.working_dir or (spec.ebs.mount_path if spec.ebs else None),
            security_context=client.V1SecurityContext(
                allow_privilege_escalation=False,
                capabilities=client.V1Capabilities(drop=["ALL"]),
            ),
        )
        return client.V1PodSpec(
            automount_service_account_token=False,
            enable_service_links=False,
            restart_policy=restart_policy,
            runtime_class_name=self._runtime_class(spec),
            node_selector=self._node_selector(spec) or None,
            service_account_name=self._service_account(spec),
            containers=[container],
            volumes=volumes or None,
            security_context=client.V1PodSecurityContext(seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")),
            termination_grace_period_seconds=10,
        )

    def _ensure_pvc(self, name: str, storage: EBSBlockStorage) -> None:
        from kubernetes import client

        pvc_name = self._pvc_name(name, storage)
        pvc = client.V1PersistentVolumeClaim(
            api_version="v1",
            kind="PersistentVolumeClaim",
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                namespace=self.namespace,
                labels={MANAGED_LABEL: "true", NAME_LABEL: name, KIND_LABEL: "storage"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=storage.storage_class_name,
                resources=client.V1VolumeResourceRequirements(requests={"storage": f"{storage.size_gib}Gi"}),
            ),
        )
        try:
            self._core_api().create_namespaced_persistent_volume_claim(self.namespace, pvc)
        except Exception as exc:
            if self._status_code(exc) != 409:
                raise

    def _validate_service_account(self, spec: SandboxSpec) -> None:
        service_account = self._service_account(spec)
        if service_account is None:
            return
        try:
            self._core_api().read_namespaced_service_account(service_account, self.namespace)
        except Exception as exc:
            if self._status_code(exc) == 404:
                raise ValueError(
                    f"Service account {self.namespace}/{service_account} does not exist. "
                    "Create it (and attach IRSA for S3), or select an existing account with --service-account."
                ) from exc
            raise

    def _service_account(self, spec: SandboxSpec) -> str | None:
        return spec.service_account_name or self.service_account_name or ("gvisor-sandbox" if spec.s3 else None)

    def _wait_for_sandbox(self, name: str, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            pod = self._sandbox_pod(name, require=False)
            if pod is not None and self._pod_ready(pod):
                return
            if pod is not None:
                last_error = self._scheduling_error(pod.metadata.name) or last_error
            time.sleep(1)
        message = f"Sandbox {name} did not become ready within {timeout_seconds}s"
        if last_error:
            raise SchedulingError(f"{message}; last scheduling error: {last_error}")
        raise TimeoutError(message)

    def _scale(self, name: str, replicas: int) -> None:
        self._apps_api().patch_namespaced_deployment_scale(
            self._validate_name(name), self.namespace, {"spec": {"replicas": replicas}}
        )

    def _sandbox_pod(self, name: str, *, require: bool):
        pods = self._core_api().list_namespaced_pod(
            self.namespace, label_selector=f"{NAME_LABEL}={name},{KIND_LABEL}=sandbox"
        ).items
        active = [pod for pod in pods if pod.metadata.deletion_timestamp is None]
        pod = active[0] if active else None
        if pod is None and require:
            raise RuntimeError(f"Sandbox {name!r} is stopped or has no active pod")
        return pod

    def _job_pod(self, name: str, *, require: bool = True):
        pods = self._core_api().list_namespaced_pod(
            self.namespace, label_selector=f"job-name={name}"
        ).items
        pod = pods[0] if pods else None
        if pod is None and require:
            raise RuntimeError(f"Job {name!r} has no pod yet")
        return pod

    def _scheduling_error(self, pod_name: str) -> str | None:
        try:
            events = self._core_api().list_namespaced_event(
                self.namespace,
                field_selector=f"involvedObject.name={pod_name},involvedObject.kind=Pod",
            )
        except Exception:
            return None
        messages = [event.message for event in events.items if event.reason == "FailedScheduling" and event.message]
        return messages[-1] if messages else None

    def _node_selector(self, spec: SandboxSpec) -> dict[str, str]:
        selector: dict[str, str] = {}
        if self._runtime_class(spec) == "gvisor":
            selector.update(self.cpu_node_selector)
        if spec.gpu is not None:
            selector.update(self.gpu_node_selector)
            selector.update(spec.gpu.node_selector)
        selector.update(spec.node_selector)
        return selector

    @staticmethod
    def _runtime_class(spec: SandboxSpec) -> str | None:
        if spec.runtime == RuntimeMode.AUTO:
            return "gvisor-nvproxy" if spec.gpu is not None else "gvisor"
        if spec.runtime == RuntimeMode.GVISOR:
            return "gvisor"
        if spec.runtime == RuntimeMode.GVISOR_NVPROXY:
            return "gvisor-nvproxy"
        return None

    def _annotations(self, spec: SandboxSpec) -> dict[str, str]:
        annotations = {SPEC_ANNOTATION: json.dumps(self._spec_payload(spec), separators=(",", ":"))}
        if spec.ttl_seconds:
            expires = datetime.now(timezone.utc) + timedelta(seconds=spec.ttl_seconds)
            annotations[EXPIRES_ANNOTATION] = expires.isoformat()
        return annotations

    @staticmethod
    def _labels(name: str, kind: str, extra: Mapping[str, str]) -> dict[str, str]:
        labels = dict(extra)
        labels.update({MANAGED_LABEL: "true", NAME_LABEL: name, KIND_LABEL: kind, "app": "sandbox-platform"})
        return labels

    @staticmethod
    def _spec_payload(spec: SandboxSpec) -> dict:
        payload = asdict(spec)
        payload["runtime"] = RuntimeMode(spec.runtime).value
        return payload

    @staticmethod
    def _stored_spec(annotations: Mapping[str, str]) -> SandboxSpec:
        raw = json.loads(annotations[SPEC_ANNOTATION])
        raw["resources"] = ComputeResources(**raw.pop("resources"))
        if raw.get("gpu"):
            raw["gpu"] = GPU(**raw["gpu"])
        if raw.get("s3"):
            raw["s3"] = S3ObjectStorage(**raw["s3"])
        if raw.get("ebs"):
            raw["ebs"] = EBSBlockStorage(**raw["ebs"])
        return SandboxSpec(**raw)

    @staticmethod
    def _pvc_name(name: str, storage: EBSBlockStorage) -> str:
        return storage.pvc_name or f"{name}-workspace"

    @staticmethod
    def _pod_ready(pod) -> bool:
        return pod.status.phase == "Running" and any(
            condition.type == "Ready" and condition.status == "True" for condition in (pod.status.conditions or [])
        )

    @staticmethod
    def _exit_code(pod) -> int | None:
        for status in pod.status.container_statuses or []:
            terminated = status.state.terminated if status.state else None
            if terminated is not None:
                return terminated.exit_code
        return None

    @staticmethod
    def _normalize_text(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        text = str(value)
        if text.startswith(("b'", 'b"')):
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return text
            if isinstance(parsed, bytes):
                return parsed.decode("utf-8", errors="replace")
        return text

    @staticmethod
    def _is_expired(annotations: Mapping[str, str], now: datetime) -> bool:
        raw = annotations.get(EXPIRES_ANNOTATION)
        return bool(raw and datetime.fromisoformat(raw) <= now)

    @staticmethod
    def _validate_name(name: str) -> str:
        normalized = name.strip().lower()
        if len(normalized) > 63 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", normalized):
            raise ValueError("Names must be valid Kubernetes DNS labels (lowercase letters, numbers, and hyphens)")
        return normalized

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        return getattr(exc, "status", None)

    def _apis(self):
        if self._core is None:
            from kubernetes import client, config
            from kubernetes.config.config_exception import ConfigException

            try:
                config.load_incluster_config()
            except ConfigException:
                config.load_kube_config(context=self.kube_context)
            self._core = client.CoreV1Api()
            self._apps = client.AppsV1Api()
            self._batch = client.BatchV1Api()
        return self._core, self._apps, self._batch

    def _core_api(self):
        return self._apis()[0]

    def _apps_api(self):
        return self._apis()[1]

    def _batch_api(self):
        return self._apis()[2]


class SandboxHandle:
    """Agent-friendly connection to one reusable sandbox."""

    def __init__(self, platform: SandboxPlatform, name: str) -> None:
        self.platform = platform
        self.name = name

    def status(self) -> SandboxStatus:
        return self.platform.status(self.name)

    def start(self, *, wait: bool = True, timeout_seconds: int = 600) -> SandboxStatus:
        return self.platform.start(self.name, wait=wait, timeout_seconds=timeout_seconds)

    def stop(self, *, wait: bool = True, timeout_seconds: int = 300) -> SandboxStatus:
        return self.platform.stop(self.name, wait=wait, timeout_seconds=timeout_seconds)

    def delete(self, *, delete_storage: bool = False) -> None:
        self.platform.delete(self.name, delete_storage=delete_storage)

    def exec(self, command: Sequence[str], *, timeout_seconds: int = 300) -> str:
        pod = self.platform._sandbox_pod(self.name, require=True)
        return stream(
            self.platform._core_api().connect_get_namespaced_pod_exec,
            pod.metadata.name,
            self.platform.namespace,
            container="sandbox",
            command=list(command),
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _request_timeout=timeout_seconds,
        )

    def run(self, command: Sequence[str], *, timeout_seconds: int = 300) -> CommandResult:
        marker = f"__SANDBOX_EXIT_{uuid.uuid4().hex}__"
        shell_command = shlex.join(list(command))
        output = self.exec(
            ["sh", "-lc", f"{shell_command}; code=$?; printf '\\n{marker}%s\\n' \"$code\""],
            timeout_seconds=timeout_seconds,
        )
        before, separator, after = output.rpartition(marker)
        if not separator:
            raise RuntimeError("Sandbox command ended without an exit-code marker")
        return CommandResult(output=before.rstrip("\r\n"), exit_code=int(after.strip()))

    def run_python(self, code: str, *, timeout_seconds: int = 300) -> CommandResult:
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        return self.run(
            ["python", "-c", f"import base64; exec(compile(base64.b64decode('{encoded}'), '<agent>', 'exec'))"],
            timeout_seconds=timeout_seconds,
        )

    def put_bytes(self, path: str, content: bytes, *, timeout_seconds: int = 300) -> None:
        encoded = base64.b64encode(content).decode("ascii")
        code = (
            "import base64, pathlib; "
            f"p=pathlib.Path({path!r}); p.parent.mkdir(parents=True, exist_ok=True); "
            f"p.write_bytes(base64.b64decode('{encoded}'))"
        )
        result = self.run(["python", "-c", code], timeout_seconds=timeout_seconds)
        if not result.ok:
            raise RuntimeError(result.output)

    def get_bytes(self, path: str, *, timeout_seconds: int = 300) -> bytes:
        encoded = self.exec(
            ["python", "-c", f"import base64, pathlib; print(base64.b64encode(pathlib.Path({path!r}).read_bytes()).decode())"],
            timeout_seconds=timeout_seconds,
        )
        return base64.b64decode(encoded.strip())

    def put_text(self, path: str, content: str, *, timeout_seconds: int = 300) -> None:
        self.put_bytes(path, content.encode("utf-8"), timeout_seconds=timeout_seconds)

    def get_text(self, path: str, *, timeout_seconds: int = 300) -> str:
        return self.get_bytes(path, timeout_seconds=timeout_seconds).decode("utf-8")

    def upload_file(self, local_path: str | Path, remote_path: str, *, timeout_seconds: int = 300) -> None:
        self.put_bytes(remote_path, Path(local_path).read_bytes(), timeout_seconds=timeout_seconds)

    def download_file(self, remote_path: str, local_path: str | Path, *, timeout_seconds: int = 300) -> Path:
        destination = Path(local_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.get_bytes(remote_path, timeout_seconds=timeout_seconds))
        return destination


class JobHandle:
    """Connection to a submitted short or long-running Kubernetes Job."""

    def __init__(self, platform: SandboxPlatform, name: str) -> None:
        self.platform = platform
        self.name = name

    def status(self) -> str:
        job = self.platform._batch_api().read_namespaced_job(self.name, self.platform.namespace)
        if job.status.succeeded:
            return "Succeeded"
        if job.status.failed:
            return "Failed"
        if job.status.active:
            return "Running"
        return "Pending"

    def logs(self) -> str:
        pod = self.platform._job_pod(self.name)
        return self.platform._normalize_text(
            self.platform._core_api().read_namespaced_pod_log(pod.metadata.name, self.platform.namespace)
        )

    def wait(self, *, timeout_seconds: int = 3600) -> RunResult:
        deadline = time.monotonic() + timeout_seconds
        last_error: str | None = None
        while time.monotonic() < deadline:
            phase = self.status()
            pod = self.platform._job_pod(self.name, require=False)
            if phase in {"Succeeded", "Failed"} and pod is not None:
                return RunResult(
                    name=self.name,
                    namespace=self.platform.namespace,
                    phase=phase,
                    exit_code=self.platform._exit_code(pod),
                    logs=self.logs(),
                    node_name=pod.spec.node_name,
                    runtime_class_name=pod.spec.runtime_class_name,
                    image=pod.spec.containers[0].image,
                    gpu=self.platform._stored_spec(
                        self.platform._batch_api()
                        .read_namespaced_job(self.name, self.platform.namespace)
                        .metadata.annotations
                    ).gpu,
                )
            if pod is not None:
                last_error = self.platform._scheduling_error(pod.metadata.name) or last_error
            time.sleep(1)
        message = f"Job {self.name} did not finish within {timeout_seconds}s"
        if last_error:
            raise SchedulingError(f"{message}; last scheduling error: {last_error}")
        raise TimeoutError(message)

    def delete(self, *, delete_storage: bool = False) -> None:
        annotations: dict[str, str] = {}
        try:
            job = self.platform._batch_api().read_namespaced_job(self.name, self.platform.namespace)
            annotations = job.metadata.annotations or {}
            self.platform._batch_api().delete_namespaced_job(
                self.name,
                self.platform.namespace,
                propagation_policy="Background",
            )
        except Exception as exc:
            if self.platform._status_code(exc) != 404:
                raise
        if delete_storage and annotations:
            spec = self.platform._stored_spec(annotations)
            if spec.ebs:
                pvc_name = self.platform._pvc_name(self.name, spec.ebs)
                try:
                    self.platform._core_api().delete_namespaced_persistent_volume_claim(
                        pvc_name, self.platform.namespace
                    )
                except Exception as exc:
                    if self.platform._status_code(exc) != 404:
                        raise
