import subprocess
from time import monotonic

from theia.errors import OperationCancelled


def run_process(args, *, cancel=None, input, timeout, **kwargs):
    if cancel is None:
        return subprocess.run(args, input=input, timeout=timeout, check=True, **kwargs)
    if cancel.is_set():
        raise OperationCancelled

    deadline = monotonic() + timeout
    with subprocess.Popen(args, stdin=subprocess.PIPE, **kwargs) as process:
        pending = input
        try:
            while not cancel.is_set():
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    stdout, stderr = process.communicate(pending, timeout=min(remaining, 0.1))
                except subprocess.TimeoutExpired:
                    pending = None  # communicate retains the original input and offset.
                    continue
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, args, stdout, stderr)
                if cancel.is_set():
                    break
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            raise OperationCancelled
        finally:
            if process.poll() is None:
                process.kill()
