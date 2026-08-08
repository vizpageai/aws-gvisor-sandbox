from __future__ import annotations

import ast
import time
import uuid
from collections.abc import Mapping

from .types import GPU, RunResult, SchedulingError, UnsupportedConfiguration


class GvisorSandbox:
    """Run Python code in Kubernetes pods using a RuntimeClass.

    By default, this targets the `gvisor` RuntimeClass and the node label used
    by this repository's EKS deployment.
    """

    def __init__(
        self,
        namespace: str = "default",
        runtime_class: str | None = "gvisor",
        node_selector: Mapping[str, str] | None = None,
        kube_context: str | None = None,
        service_account_name: str | None = None,
        cleanup: bool = True,
        allow_gpu_with_gvisor: bool = False,
    ) -> None:
        self.namespace = namespace
        self.runtime_class = runtime_class
        self.node_selector = dict(node_selector or {"runtime.gvisor.dev/enabled": "true"})
        self.kube_context = kube_context
        self.service_account_name = service_account_name
        self.cleanup = cleanup
        self.allow_gpu_with_gvisor = allow_gpu_with_gvisor
        self._core_api = None

    def run_python(
        self,
        code: str,
        *,
        image: str = "python:3.12-alpine",
        name: str | None = None,
        timeout_seconds: int = 300,
        env: Mapping[str, str] | None = None,
        labels: Mapping[str, str] | None = None,
        gpu: GPU | str | None = None,
        cleanup: bool | None = None,
        fail_fast_unschedulable: bool = False,
    ) -> RunResult:
        gpu_spec = self._normalize_gpu(gpu)
        self._validate_gpu_runtime(gpu_spec)

        pod_name = name or f"gvisor-run-{uuid.uuid4().hex[:10]}"
        pod = self._build_pod(
            name=pod_name,
            image=image,
            code=code,
            env=dict(env or {}),
            labels=dict(labels or {}),
            gpu=gpu_spec,
        )

        api = self._api()
        api.create_namespaced_pod(namespace=self.namespace, body=pod)
        try:
            phase = self._wait_for_terminal_phase(
                api,
                pod_name,
                timeout_seconds,
                fail_fast_unschedulable=fail_fast_unschedulable,
            )
            pod_obj = api.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            logs = self._normalize_logs(api.read_namespaced_pod_log(name=pod_name, namespace=self.namespace))
            exit_code = self._exit_code(pod_obj)
            return RunResult(
                name=pod_name,
                namespace=self.namespace,
                phase=phase,
                exit_code=exit_code,
                logs=logs,
                node_name=pod_obj.spec.node_name,
                runtime_class_name=pod_obj.spec.runtime_class_name,
                image=image,
                gpu=gpu_spec,
            )
        finally:
            should_cleanup = self.cleanup if cleanup is None else cleanup
            if should_cleanup:
                self.delete_pod(pod_name, ignore_not_found=True)

    def delete_pod(self, name: str, ignore_not_found: bool = True) -> None:
        api = self._api()
        try:
            api.delete_namespaced_pod(name=name, namespace=self.namespace)
        except Exception as exc:
            if not ignore_not_found or not self._is_not_found(exc):
                raise

    def available_gpu_nodes(self, gpu: GPU | str, node_selector: Mapping[str, str] | None = None) -> list[str]:
        """Return nodes that expose the requested GPU resource and match selectors."""

        gpu_spec = self._normalize_gpu(gpu)
        if gpu_spec is None:
            return []

        selector = dict(self.node_selector if node_selector is None else node_selector)
        selector.update(gpu_spec.node_selector)
        matches: list[str] = []

        for node in self._api().list_node().items:
            labels = node.metadata.labels or {}
            if any(labels.get(key) != value for key, value in selector.items()):
                continue

            allocatable = node.status.allocatable or {}
            raw_count = allocatable.get(gpu_spec.resource_name, "0")
            try:
                available = int(raw_count)
            except (TypeError, ValueError):
                available = 0
            if available >= gpu_spec.count:
                matches.append(node.metadata.name)

        return matches

    def require_gpu_nodes(self, gpu: GPU | str, node_selector: Mapping[str, str] | None = None) -> list[str]:
        nodes = self.available_gpu_nodes(gpu, node_selector=node_selector)
        if not nodes:
            gpu_spec = self._normalize_gpu(gpu)
            assert gpu_spec is not None
            raise SchedulingError(
                "No schedulable nodes expose "
                f"{gpu_spec.resource_name} >= {gpu_spec.count} with the requested node selector."
            )
        return nodes

    def _build_pod(
        self,
        *,
        name: str,
        image: str,
        code: str,
        env: dict[str, str],
        labels: dict[str, str],
        gpu: GPU | None,
    ):
        from kubernetes import client

        merged_labels = {"app": "gvisor-sandbox", "gvisor-sandbox/run": name}
        merged_labels.update(labels)

        node_selector = dict(self.node_selector)
        resources = None
        if gpu is not None:
            node_selector.update(gpu.node_selector)
            env.setdefault("GVISOR_SANDBOX_GPU_TYPE", gpu.type)
            resources = client.V1ResourceRequirements(
                limits={gpu.resource_name: str(gpu.count)},
                requests={gpu.resource_name: str(gpu.count)},
            )

        container = client.V1Container(
            name="runner",
            image=image,
            command=["python", "-c", code],
            env=[client.V1EnvVar(name=key, value=value) for key, value in sorted(env.items())],
            resources=resources,
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
            node_selector=node_selector or None,
            service_account_name=self.service_account_name,
            containers=[container],
            security_context=client.V1PodSecurityContext(seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")),
            termination_grace_period_seconds=10,
        )
        metadata = client.V1ObjectMeta(name=name, namespace=self.namespace, labels=merged_labels)
        return client.V1Pod(api_version="v1", kind="Pod", metadata=metadata, spec=spec)

    def _api(self):
        if self._core_api is None:
            from kubernetes import client, config
            from kubernetes.config.config_exception import ConfigException

            try:
                config.load_incluster_config()
            except ConfigException:
                config.load_kube_config(context=self.kube_context)
            self._core_api = client.CoreV1Api()
        return self._core_api

    def _wait_for_terminal_phase(
        self,
        api,
        pod_name: str,
        timeout_seconds: int,
        *,
        fail_fast_unschedulable: bool = False,
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        last_phase = "Pending"
        last_unschedulable: str | None = None
        while time.monotonic() < deadline:
            pod = api.read_namespaced_pod(name=pod_name, namespace=self.namespace)
            last_phase = pod.status.phase or last_phase
            if last_phase in {"Succeeded", "Failed"}:
                return last_phase
            if last_phase == "Pending":
                unschedulable = self._unschedulable_message(api, pod_name)
                if unschedulable:
                    last_unschedulable = unschedulable
                    if fail_fast_unschedulable:
                        raise SchedulingError(f"Pod {pod_name} is unschedulable: {unschedulable}")
            time.sleep(1)
        message = f"Pod {pod_name} did not complete within {timeout_seconds}s; last phase={last_phase}"
        if last_unschedulable:
            raise SchedulingError(f"{message}; last scheduling error: {last_unschedulable}")
        raise TimeoutError(message)

    def _unschedulable_message(self, api, pod_name: str) -> str | None:
        try:
            events = api.list_namespaced_event(
                namespace=self.namespace,
                field_selector=f"involvedObject.name={pod_name},involvedObject.kind=Pod",
            )
        except Exception:
            return None

        messages: list[str] = []
        for event in events.items:
            if event.reason == "FailedScheduling" and event.message:
                messages.append(event.message)
        return messages[-1] if messages else None

    def _exit_code(self, pod) -> int | None:
        statuses = pod.status.container_statuses or []
        for status in statuses:
            terminated = status.state.terminated if status.state else None
            if terminated is not None:
                return terminated.exit_code
        return None

    def _normalize_logs(self, logs) -> str:
        if isinstance(logs, bytes):
            return logs.decode("utf-8", errors="replace")
        text = str(logs)
        if text.startswith(("b'", 'b"')):
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return text
            if isinstance(parsed, bytes):
                return parsed.decode("utf-8", errors="replace")
        return text

    def _normalize_gpu(self, gpu: GPU | str | None) -> GPU | None:
        if gpu is None:
            return None
        if isinstance(gpu, GPU):
            return gpu
        return GPU.from_type(gpu)

    def _validate_gpu_runtime(self, gpu: GPU | None) -> None:
        if gpu is None:
            return
        if self.runtime_class == "gvisor" and not self.allow_gpu_with_gvisor:
            raise UnsupportedConfiguration(
                "GPU workloads cannot be safely assumed to run inside gVisor. "
                "Use a GPU-capable non-gVisor RuntimeClass or set "
                "allow_gpu_with_gvisor=True only after validating your runtime and device plugin."
            )

    def _is_not_found(self, exc: Exception) -> bool:
        status = getattr(exc, "status", None)
        return status == 404
