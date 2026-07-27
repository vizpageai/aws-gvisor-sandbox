from __future__ import annotations

from dataclasses import dataclass, field


class UnsupportedConfiguration(ValueError):
    """Raised when a requested sandbox configuration is not supported."""


class SchedulingError(TimeoutError):
    """Raised when Kubernetes cannot schedule a sandbox pod."""


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
            return cls(type=normalized, count=count, resource_name="nvidia.com/gpu")
        raise ValueError(
            "Unknown GPU type. Use a Kubernetes extended resource such as "
            "'nvidia.com/gpu' or an NVIDIA type such as 'nvidia-l4'."
        )


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
