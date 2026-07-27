from gvisor_sandbox import GvisorSandbox

sandbox = GvisorSandbox()

result = sandbox.run_python(
    """
import sys
import subprocess

subprocess.check_call([
    sys.executable, "-m", "pip", "install", "numpy"
])

import numpy as np
x = np.random.randn()
print(x)
""",
    image="python:3.12",
    timeout_seconds=300,
)

print(result.logs)