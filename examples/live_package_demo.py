from gvisor_sandbox import GvisorSandbox


result = GvisorSandbox().run_python(
    """
import platform

print("package demo running")
print("kernel:", platform.release())
print("value:", sum(range(7)))
""",
    name="gvisor-package-live-demo",
)

print(result.logs)
print("phase:", result.phase)
print("exit:", result.exit_code)
print("node:", result.node_name)
print("runtime:", result.runtime_class_name)

