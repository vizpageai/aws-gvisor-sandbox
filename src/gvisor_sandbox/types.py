from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class UnsupportedConfiguration(ValueError):
    """Raised when a requested sandbox configuration is not supported."""


class SchedulingError(TimeoutError):
    """Raised when Kubernetes cannot schedule a sandbox pod."""


class RuntimeMode(str, Enum):
    """Container runtime selection for a sandbox workload."""

    AUTO = "auto"
    GVISOR = "gvisor"
    NATIVE = "native"


@dataclass(frozen=True)
class GPU:
    """Kubernetes GPU resource declaration.

    The common NVIDIA device plugin resource is `nvidia.com/gpu`. The `type`
    field is kept as metadata for scheduling labels and logs; Kubernetes itself
    schedules GPU devices through the `resource_name` and `count`.
    """

    type: str
    count: int = 1
    resource_name: str = "nvidia.com/gpu"
    node_selector: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("GPU count must be at least 1")
        if "/" not in self.resource_name:
            raise ValueError("GPU resource_name must be a Kubernetes extended resource such as nvidia.com/gpu")

    @classmethod
    def from_type(cls, gpu_type: str, count: int = 1) -> "GPU":
        normalized = gpu_type.strip().lower()
        if not normalized:
            raise ValueError("gpu_type cannot be empty")
        if "/" in normalized:
            return cls(type=normalized, count=count, resource_name=normalized)
        if normalized.startswith("nvidia"):
            return cls(
                type=normalized,
                count=count,
                resource_name="nvidia.com/gpu",
                node_selector={"accelerator": normalized},
            )
        raise ValueError(
            "Unknown GPU type. Use a Kubernetes extended resource such as "
            "'nvidia.com/gpu' or an NVIDIA type such as 'nvidia-l4'."
        )


@dataclass(frozen=True)
class S3ObjectStorage:
    """S3-backed object workspace metadata exposed to sandbox containers.

    This is the AWS equivalent of Azure Blob-backed shared sandbox storage. It
    is intentionally exposed as object storage metadata rather than a POSIX
    filesystem mount; use the AWS SDK/CLI or Mountpoint for Amazon S3 CSI if
    the workload needs file-like access.
    """

    bucket: str
    prefix: str = ""
    region: str | None = None

    @classmethod
    def from_uri(cls, uri: str, *, region: str | None = None) -> "S3ObjectStorage":
        if not uri.startswith("s3://"):
            raise ValueError("S3 URI must start with s3://")
        path = uri[5:]
        bucket, _, prefix = path.partition("/")
        if not bucket:
            raise ValueError("S3 URI must include a bucket name")
        return cls(bucket=bucket, prefix=prefix.strip("/"), region=region)

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}" if self.prefix else f"s3://{self.bucket}"


@dataclass(frozen=True)
class EBSBlockStorage:
    """EBS-backed persistent block storage request for a sandbox.

    Kubernetes provisions this through a PersistentVolumeClaim, usually via the
    AWS EBS CSI driver and a gp3 StorageClass.
    """

    size_gib: int = 8
    storage_class_name: str = "gp3"
    mount_path: str = "/workspace"
    pvc_name: str | None = None

    def __post_init__(self) -> None:
        if self.size_gib < 1:
            raise ValueError("EBS block storage size must be at least 1 GiB")
        if not self.mount_path.startswith("/"):
            raise ValueError("EBS block storage mount_path must be absolute")


@dataclass(frozen=True)
class ComputeResources:
    """CPU, memory, and ephemeral disk requests and limits."""

    cpu: str = "500m"
    memory: str = "1Gi"
    ephemeral_storage: str | None = None
    cpu_limit: str | None = None
    memory_limit: str | None = None


@dataclass(frozen=True)
class SandboxSpec:
    """Portable definition for a reusable sandbox or Kubernetes job."""

    image: str = "python:3.12-slim"
    runtime: RuntimeMode | str = RuntimeMode.AUTO
    gpu: GPU | None = None
    resources: ComputeResources = field(default_factory=ComputeResources)
    node_selector: dict[str, str] = field(default_factory=dict)
    service_account_name: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    s3: S3ObjectStorage | None = None
    ebs: EBSBlockStorage | None = None
    working_dir: str | None = None
    ttl_seconds: int | None = None

    def __post_init__(self) -> None:
        runtime = RuntimeMode(self.runtime)
        object.__setattr__(self, "runtime", runtime)
        if runtime == RuntimeMode.GVISOR and self.gpu is not None:
            raise UnsupportedConfiguration(
                "This platform does not provide gVisor GPU passthrough. "
                "Use runtime='auto' or runtime='native' for GPU workloads."
            )
        if self.working_dir is not None and not self.working_dir.startswith("/"):
            raise ValueError("working_dir must be an absolute container path")
        if self.ttl_seconds is not None and self.ttl_seconds < 1:
            raise ValueError("ttl_seconds must be at least 1")


@dataclass(frozen=True)
class CommandResult:
    """Result from a command executed in a reusable sandbox."""

    output: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class SandboxStatus:
    name: str
    namespace: str
    phase: str
    ready: bool
    node_name: str | None
    runtime_class_name: str | None
    image: str
    s3: S3ObjectStorage | None = None
    ebs: EBSBlockStorage | None = None
    gpu: GPU | None = None
    stopped: bool = False


@dataclass(frozen=True)
class RunResult:
    name: str
    namespace: str
    phase: str
    exit_code: int | None
    logs: str
    node_name: str | None
    runtime_class_name: str | None
    image: str
    gpu: GPU | None = None

    @property
    def ok(self) -> bool:
        return self.phase == "Succeeded" and self.exit_code == 0
