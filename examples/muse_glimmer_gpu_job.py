from __future__ import annotations

import os
import shlex

from gvisor_sandbox import GPU, ComputeResources, SandboxPlatform, SandboxSpec

model_repo = "meta-models/Muse-Glimmer-30B-GGUF"
model_file = "muse-glimmer-30B-kquant-17gb.gguf"
model_url = f"https://huggingface.co/{model_repo}/resolve/main/{model_file}"
prompt = os.getenv(
    "MUSE_GLIMMER_PROMPT",
    "Reply with exactly: gVisor isolated Muse Glimmer inference succeeded",
)

platform = SandboxPlatform(namespace=os.getenv("SANDBOX_NAMESPACE", "default"))
spec = SandboxSpec(
    image=os.getenv("MUSE_GLIMMER_IMAGE", "ghcr.io/ggml-org/llama.cpp:light-cuda12-b10362"),
    gpu=GPU.from_type("nvidia-gpu"),
    resources=ComputeResources(
        cpu="2",
        memory="10Gi",
        ephemeral_storage="30Gi",
        cpu_limit="4",
        memory_limit="12Gi",
    ),
)

script = f"""
set -eu
mkdir -p /models
model=/models/{model_file}
curl --fail --location --retry 5 --retry-all-errors --output "$model" {shlex.quote(model_url)}
test "$(stat -c %s "$model")" -gt 16000000000
/app/llama-cli --version
/app/llama-cli \
  --model "$model" \
  --n-gpu-layers 99 \
  --ctx-size 2048 \
  --jinja \
  --single-turn \
  --n-predict 96 \
  --temp 0.2 \
  --prompt {shlex.quote(prompt)}
"""

job_name = os.getenv("SANDBOX_JOB_NAME", "muse-glimmer-gpu-test")
try:
    result = platform.submit_command(
        ["sh", "-lc", script],
        name=job_name,
        spec=spec,
        timeout_seconds=3600,
        ttl_seconds_after_finished=3600,
    )
    print(f"runtime={result.runtime_class_name} node={result.node_name}")
    print(result.logs)
    expected = "gVisor isolated Muse Glimmer inference succeeded"
    if not result.ok or result.runtime_class_name != "gvisor-nvproxy" or expected not in result.logs:
        raise RuntimeError(f"Muse Glimmer test failed: {result}")
finally:
    try:
        platform.job(job_name).delete()
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise
