from gvisor_sandbox import GvisorSandbox


sandbox = GvisorSandbox()

result = sandbox.run_python(
    """
import subprocess
import sys
import time

subprocess.check_call([
    sys.executable,
    "-m",
    "pip",
    "install",
    "numpy",
    "--quiet",
])

import numpy as np

rng = np.random.default_rng(42)

samples = 12_000
features = 32
epochs = 25
learning_rate = 0.25

true_weights = rng.normal(size=features)
x = rng.normal(size=(samples, features))
logits = x @ true_weights + 0.35 * rng.normal(size=samples)
y = (logits > 0).astype(np.float64)

weights = np.zeros(features)
bias = 0.0

start = time.perf_counter()
for epoch in range(epochs):
    prediction = 1.0 / (1.0 + np.exp(-(x @ weights + bias)))
    error = prediction - y
    weights -= learning_rate * (x.T @ error) / samples
    bias -= learning_rate * error.mean()

    if epoch in {0, 4, 9, 14, 19, 24}:
        loss = -np.mean(y * np.log(prediction + 1e-9) + (1 - y) * np.log(1 - prediction + 1e-9))
        accuracy = ((prediction >= 0.5) == y).mean()
        print(f"epoch={epoch + 1:02d} loss={loss:.4f} accuracy={accuracy:.4f}")

train_seconds = time.perf_counter() - start

test_x = rng.normal(size=(2_000, features))
test_y = (test_x @ true_weights > 0).astype(np.float64)
test_prediction = 1.0 / (1.0 + np.exp(-(test_x @ weights + bias)))
test_accuracy = ((test_prediction >= 0.5) == test_y).mean()

print("numpy:", np.__version__)
print("train_seconds:", round(train_seconds, 3))
print("test_accuracy:", round(float(test_accuracy), 4))
print("batch_predictions:", np.round(test_prediction[:8], 4).tolist())
""",
    image="python:3.12",
    name="gvisor-ml-workload-demo",
    timeout_seconds=600,
)

print(result.logs)
print("phase:", result.phase)
print("exit:", result.exit_code)
print("node:", result.node_name)
print("runtime:", result.runtime_class_name)
