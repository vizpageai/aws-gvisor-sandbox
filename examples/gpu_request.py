from gvisor_sandbox import GPU, GvisorSandbox

# GPU requests use gVisor nvproxy. The cluster still needs GPU nodes and a
# device plugin such as the NVIDIA Kubernetes device plugin.
sandbox = GvisorSandbox(
    node_selector={"accelerator": "nvidia-gpu"},
)

print("matching GPU nodes:", sandbox.require_gpu_nodes(GPU.from_type("nvidia-gpu", count=1)))

result = sandbox.run_python(
    """
import os

print("gpu type:", os.environ["GVISOR_SANDBOX_GPU_TYPE"])
print("run your CUDA/PyTorch code here")
""",
    image="python:3.12",
    gpu=GPU.from_type("nvidia-gpu", count=1),
)

print(result.logs)
