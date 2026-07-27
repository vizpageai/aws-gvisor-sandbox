from gvisor_sandbox import GvisorSandbox


sandbox = GvisorSandbox(cleanup=False)
result = sandbox.run_python(
    """
import platform

print("hello from the gvisor-sandbox package")
print("kernel:", platform.release())
print("value:", sum(range(10)))
""",
    name="gvisor-package-demo",
)

print(result.logs)
print("pod:", result.name)
print("node:", result.node_name)
print("runtime:", result.runtime_class_name)

