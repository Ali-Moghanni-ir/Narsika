"""Test-only pseudo-terminal helper. Never forwards credential-bearing output to logs."""
import os
import pty
import select
import subprocess
import time


def run_terminal(command, *, env=None, cwd=None, timeout=90):
    master, slave = pty.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd)
    os.close(slave)
    chunks = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], .1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            elif process.poll() is not None:
                break
        if process.poll() is None:
            process.wait(timeout=max(1, deadline-time.monotonic()))
        if process.returncode:
            raise RuntimeError(f'Terminal command failed with exit {process.returncode}; output withheld because it may contain a password.')
        return b''.join(chunks).decode(errors='replace')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
