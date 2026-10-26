"""Visual gate container runner for UI regression testing.

Public interface:
- run_visual_gate(workdir, visual_cmd, visual_image, timeout_sec=300, out_dir=None, docker_bin="docker") -> tuple[bool, str, list[str]]

Invariants:
- Returns immediately with (True, "", []) when visual_cmd is empty.
- Verifies visual_image presence with docker image inspect before running container.
- Runs container isolated: no network, read-only root with a small tmpfs /tmp, no capabilities, memory/cpu/pid limits,
  workdir mounted read-only; a timed-out container is killed by name (no orphans).
- Mounts output directory at /out (made writable for the container's user) and collects sorted *.png basenames on exit 0.
- Extracts last 60 lines of combined output on container failure.
"""
import os
import subprocess
import tempfile
import time


def _collect_pngs(out_dir: str) -> list[str]:
    """Scan directory for *.png files and return sorted basenames."""
    pngs = []
    if os.path.isdir(out_dir):
        for entry in os.scandir(out_dir):
            if entry.is_file() and entry.name.endswith(".png"):
                pngs.append(entry.name)
        pngs.sort()
    return pngs


def _extract_tail(stdout: str | None, stderr: str | None, max_lines: int = 60) -> str:
    """Extract up to max_lines trailing lines of combined output."""
    combined = ""
    if stdout:
        combined += stdout
    if stderr:
        if combined and not combined.endswith("\n"):
            combined += "\n"
        combined += stderr
    lines = combined.strip().splitlines()
    return "\n".join(lines[-max_lines:]) if lines else ""


def _run_container(
    workdir: str,
    visual_cmd: str,
    visual_image: str,
    timeout_sec: int,
    target_out: str,
    docker_bin: str,
) -> tuple[bool, str, list[str]]:
    """Execute container for visual check and collect output and artifacts."""
    abs_workdir = os.path.abspath(workdir)
    try:
        os.chmod(target_out, 0o777)          # the container user is not the foreman's: without this /out is read-only to it and no screenshot is stored
    except OSError:
        pass
    name = f"visual-gate-{os.getpid()}-{int(time.time() * 1000)}"
    cmd = [
        docker_bin,
        "run",
        "--rm",
        "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=512m",
        "-e", "HOME=/tmp",
        "--memory", "2g",
        "--cpus", "2",
        "--pids-limit", "512",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "-v", f"{abs_workdir}:{abs_workdir}:ro",
        "-v", f"{target_out}:/out:rw",
        "-w", abs_workdir,
        visual_image,
        "sh",
        "-c",
        visual_cmd,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        subprocess.run([docker_bin, "kill", name], capture_output=True)
        return (False, f"Visual gate timed out after {timeout_sec}s", [])
    except (subprocess.SubprocessError, OSError) as e:
        return (False, f"Visual gate error: {e}", [])

    tail = _extract_tail(proc.stdout, proc.stderr, max_lines=60)
    if proc.returncode != 0:
        return (False, tail, [])

    return (True, tail, _collect_pngs(target_out))


def run_visual_gate(
    workdir: str,
    visual_cmd: str,
    visual_image: str,
    timeout_sec: int = 300,
    out_dir: str | None = None,
    docker_bin: str = "docker",
) -> tuple[bool, str, list[str]]:
    """Runs visual check in isolated container. Returns (ok, output_tail, png_files)."""
    if not visual_cmd or not visual_cmd.strip():
        return (True, "", [])

    if not workdir or not str(workdir).strip():
        return (False, "Visual gate error: workdir is required", [])

    try:
        inspect = subprocess.run(
            [docker_bin, "image", "inspect", visual_image],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if inspect.returncode != 0:
            return (False, f"Visual gate error: image '{visual_image}' not found locally", [])
    except (subprocess.SubprocessError, OSError):
        return (False, f"Visual gate error: image '{visual_image}' not found locally", [])

    if out_dir is not None:
        os.makedirs(out_dir, exist_ok=True)
        return _run_container(workdir, visual_cmd, visual_image, timeout_sec, os.path.abspath(out_dir), docker_bin)

    with tempfile.TemporaryDirectory() as tmp_dir:
        return _run_container(workdir, visual_cmd, visual_image, timeout_sec, os.path.abspath(tmp_dir), docker_bin)
