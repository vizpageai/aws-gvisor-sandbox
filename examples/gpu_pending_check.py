from gvisor_sandbox import GPU, GvisorSandbox, SchedulingError


sandbox = GvisorSandbox(runtime_class=None, node_selector={"accelerator": "nvidia-l4"})

try:
    sandbox.run_python(
        "print('gpu workload')",
        image="python:3.12",
        gpu=GPU.from_type("nvidia-l4", count=1),
        timeout_seconds=30,
    )
except SchedulingError as exc:
    # FailedScheduling events are no longer treated as immediately terminal.
    # This exception is raised only after timeout_seconds expires, or if
    # fail_fast_unschedulable=True is passed to run_python().
    print(type(exc).__name__)
    print(exc)
