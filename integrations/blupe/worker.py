"""Run a fresh isolated agent and the existing persistent session in a host child."""

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import traceback
from pathlib import Path

from integrations.blupe.environment import BluPeTask, PipeBridge
from integrations.blupe.knowledge import verify
from orchestrator import launch_agent
from orchestrator.files import codex_token_usage, write_json
from orchestrator.session import ExecutionTimeout, Session
from orchestrator.session_client import receive, send


class TraceMirror:
    """Retain Codex rollout JSONL outside its disposable, credential-bearing HOME."""

    def __init__(self, home, run):
        self.source = home / ".codex/sessions"
        self.destination = run / "agent"
        self.offsets = {}

    def sync(self):
        for source in sorted(self.source.rglob("*.jsonl")):
            relative = source.relative_to(self.source)
            target = self.destination / "rollouts" / relative
            offset = self.offsets.get(relative, 0)
            with source.open("rb") as stream:
                stream.seek(offset)
                chunk = stream.read()
            if chunk:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("ab") as stream:
                    stream.write(chunk)
                self.offsets[relative] = offset + len(chunk)
        if self.offsets:
            write_json(self.destination / "traces.json", {
                "files": [str(Path("rollouts") / p) for p in self.offsets],
                "bytes": sum(self.offsets.values()),
            })


def agent_command(run, sockets, home, settings, name):
    argv = launch_agent.docker_argv(
        run / "workspace",
        home,
        launch_agent.adapt_for_docker(
            launch_agent.agent_argv(
                "codex",
                "Read instruction.md and primitives/primitives.md. Use relevant frozen skills "
                "and lessons to complete get_instruction(). Inspect native camera observations. "
                "Use scripts/env.py to execute code; finish when done. "
                "Never reset or modify knowledge.",
                model=settings["model"],
                effort=settings["reasoning_effort"],
            ),
            preset="codex",
        ),
        image=settings["image"],
        network="filtered",
        cpus=4,
        memory_mb=8192,
        container_name=name,
    )
    mounts = [
        "--env",
        "EMBODIEDEVO_SOCKET=/run/embodiedevo/session.sock",
        "--mount",
        f"type=bind,src={sockets},dst=/run/embodiedevo,readonly",
    ]
    for component in (
        "instruction.md",
        "primitives",
        "scripts",
        "observations",
        "skills",
        "lessons",
    ):
        mounts += [
            "--mount",
            f"type=bind,src={run / 'workspace' / component},dst=/workspace/{component},readonly",
        ]
    index = argv.index(settings["image"])
    argv[index:index] = mounts
    return argv


def smoke_program(session, run):
    """Exercise an original frozen helper against the mock, never against hardware."""
    helper = run / "workspace/skills/general_pickup/move_ee.py"
    if not helper.is_file():
        raise ValueError("Smoke needs the frozen general_pickup/move_ee.py helper")
    calls = [
        [{"path": "submission/state.py", "code": "persistent_value = 41\nobs = get_observation()"}],
        [
            {"path": "skills/general_pickup/move_ee.py", "code": helper.read_text()},
            {
                "path": "submission/solution.py",
                "code": "assert persistent_value == 41\n"
                "target = list(get_observation()['state']['left_ee_pose'])\n"
                "target[2] += 0.002\n"
                "obs, movement = move_ee('left', target, 0.5, 2)\n"
                "assert movement['reason'] == 'reached'\n"
                "persistent_value += 1\nprint(movement)",
            },
        ],
        [
            {
                "path": "submission/check.py",
                "code": "assert persistent_value == 42\nprint('persistent OK')",
            }
        ],
    ]
    for files in calls:
        result = session.handle({"op": "exec", "files": files})
        if not result["ok"]:
            raise RuntimeError("Offline frozen-skill execution failed; see numbered feedback")
    session.handle({"op": "finish"})


def run_worker(connection, run_path, instruction, settings):
    run = Path(run_path)
    process = session = None
    name = f"embodiedevo-yam-{os.getpid()}-{time.time_ns()}"
    valid = False

    def interrupted(signum, frame):
        raise ExecutionTimeout("Deployment stopped by its controller")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        verify(run)
        env = BluPeTask(PipeBridge(connection, settings["duration"] + 10), instruction)
        session = Session(
            env,
            run,
            phase="test",
            seed=0,
            budget=100,
            seconds=settings["duration"],
            execution_seconds=settings["duration"],
            fps=10,
        )
        container_smoke = settings.get("container_smoke", False)
        if settings.get("simulation") and not container_smoke:
            smoke_program(session, run)
        else:
            with tempfile.TemporaryDirectory(prefix="embodiedevo-yam-") as temporary:
                root = Path(temporary)
                root.chmod(0o755)
                sockets = root / "control"
                sockets.mkdir(mode=0o755)
                if container_smoke:
                    home = root / "agent-home"
                    home.mkdir()
                    # No credentials, login or model process are used in this offline check.
                    shutil.copyfile(
                        Path(__file__).with_name("container_smoke.py"),
                        run / "workspace/submission/container_smoke.py",
                    )
                else:
                    home = launch_agent.stage_home(root / "agent-home")
                trace = TraceMirror(home, run)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    path = sockets / "session.sock"
                    listener.bind(str(path))
                    path.chmod(0o600)
                    listener.listen(8)
                    listener.settimeout(0.2)
                    command = agent_command(run, sockets, home, settings, name)
                    if container_smoke:
                        command[command.index("--network") + 1] = "none"
                        image_index = command.index(settings["image"])
                        command[image_index + 1 :] = ["python", "submission/container_smoke.py"]
                    with (run / "agent.log").open("wb") as output:
                        process = subprocess.Popen(
                            command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
                        )
                        try:
                            while not session.closed:
                                trace.sync()
                                if process.poll() is not None:
                                    session.closed = True
                                    session.valid = process.returncode == 0
                                    session.reason = (
                                        "agent_exit" if session.valid else "agent_failed"
                                    )
                                    break
                                if session.status()["seconds_remaining"] <= 0:
                                    session.closed, session.reason = True, "time_budget"
                                    session.env.truncated = True
                                    break
                                try:
                                    client, _ = listener.accept()
                                except TimeoutError:
                                    continue
                                with (
                                    client,
                                    contextlib.suppress(BrokenPipeError, ConnectionResetError),
                                ):
                                    client.settimeout(10)
                                    with client.makefile("rwb") as stream:
                                        payload = None
                                        try:
                                            payload = receive(stream)
                                            result = session.handle(payload)
                                        except (
                                            ValueError,
                                            PermissionError,
                                            ConnectionError,
                                            OSError,
                                            ExecutionTimeout,
                                        ) as exc:
                                            result = {
                                                "ok": False,
                                                "error": str(exc),
                                                "status": session.status(),
                                            }
                                        if payload is not None:
                                            session.record_rpc(payload, result)
                                        send(stream, result)
                        finally:
                            stop_and_archive_agent(process, name, trace, run, home)
                    session.save()
        verify(run)
        valid = session.valid and session.reason in {"agent_finished", "agent_exit"}
    except BaseException as exc:
        write_json(
            run / "worker-error.json",
            {"type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        if session is not None:
            session.closed, session.valid, session.reason = True, False, "worker_failed"
            session.save()
    finally:
        stop_agent(process, name)
        with contextlib.suppress(OSError, EOFError):
            connection.send({"op": "finished", "valid": valid})
        connection.close()


def stop_and_archive_agent(process, name, trace, run, home):
    # Controller cancellation can interrupt docker stop. Archive before the
    # temporary HOME is removed even when that interruption raises an exception.
    try:
        stop_agent(process, name)
    finally:
        trace.sync()
        write_json(run / "model-usage.json", codex_token_usage(home))


def stop_agent(process, name):
    if process is None:
        return
    # Stop the actual container as well as its docker CLI, even after a disconnected CLI.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["docker", "stop", "--time", "2", name], capture_output=True, timeout=5)
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
