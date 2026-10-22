"""Frozen-knowledge deployment, offline smoke and read-only remote availability."""

import argparse
import io
import json
import time
from pathlib import Path

from PIL import Image

from integrations.blupe import knowledge, upstream
from integrations.blupe.probe import probe
from orchestrator.files import append_jsonl, write_json


class SyntheticCameras:
    """Explicitly synthetic frames for transport tests; no physical perception claim."""

    def capture_for_policy(self, observation, *, cancelled):
        from remote_yam.cameras import CameraFrame

        frames = []
        for name, color in (("left", "red"), ("top", "green"), ("right", "blue")):
            data = io.BytesIO()
            Image.new("RGB", (32, 24), color).save(data, format="JPEG")
            frames.append(
                CameraFrame(name, data.getvalue(), time.time(), source_captured_at=time.time())
            )
        return frames


def execute(run, args, *, simulation):
    upstream.load()
    from remote_yam.cameras import CameraFrameSource
    from remote_yam.controller import RunnerController
    from remote_yam.session import HttpSessionAPI

    from integrations.blupe.provider import EmbodiedEvoProvider

    if simulation:
        from integrations.blupe.mock import ObservableMock

        if args.container:
            from orchestrator import launch_agent

            launch_agent.require_image(args.image)
        api = ObservableMock()
        calibration = {"robot_id": args.robot_id}
        cameras = SyntheticCameras()
    else:
        checks = probe(args.session_api, args.robot_id, run / "preflight")
        if not checks["queue_ready"]:
            write_json(
                run / "deployment.json",
                {
                    "state": "blocked_before_queue",
                    "blockers": checks["queue_blockers"],
                    "session_created": False,
                    "motion_sent": False,
                    "task_success": None,
                },
            )
            print(
                json.dumps({"state": "blocked_before_queue", "blockers": checks["queue_blockers"]})
            )
            return 2
        from orchestrator import launch_agent

        launch_agent.require_image(args.image)
        launch_agent.ensure_network()
        # Fail before entering a physical queue if the provider config is absent.
        launch_agent.agent_codex_home()
        api = HttpSessionAPI(args.session_api, robot_id=args.robot_id, supports_trajectories=True)
        calibration = checks["checks"]["calibration"]["data"]
        cameras = CameraFrameSource(args.session_api)
    settings = {
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "duration": args.duration,
        "image": args.image,
        "simulation": simulation,
        "container_smoke": simulation and args.container,
    }
    instruction = "Offline frozen helper transfer check" if simulation else args.instruction
    write_json(
        run / "deployment-config.json",
        {
            **settings,
            "robot_id": args.robot_id,
            "instruction": instruction,
            "upstream_revision": upstream.REVISION,
            "session_api": args.session_api,
            "success_evaluator": None,
        },
    )
    provider = EmbodiedEvoProvider(
        run=run,
        instruction=instruction,
        robot_id=args.robot_id,
        calibration=calibration,
        camera_source=cameras,
        settings=settings,
    )
    controller = RunnerController(
        api,
        robot_id=args.robot_id,
        hardware_control_enabled=not simulation,
        recording_root=run / "controller",
        share_conversation=False,
    )
    deadline = time.monotonic() + args.queue_timeout + args.duration + 15
    started = time.monotonic()
    status = {}
    recorder = None
    next_status = 0
    try:
        if getattr(args, "video_url", None):
            from integrations.blupe.recording import VideoRecorder

            recorder = VideoRecorder(run, args.video_url)
            recorder.start()  # Require actual video bytes before robot admission.
        controller.join_and_run(provider, instruction, run_duration_s=args.duration)
        while time.monotonic() < deadline:
            status = controller.status()
            if time.monotonic() >= next_status:
                public = {k: v for k, v in status.items() if k != "lease_id"}
                write_json(run / "controller-status.json", public)
                append_jsonl(run / "controller-status.jsonl", {"sampled_at": time.time(), "status": public})
                next_status = time.monotonic() + 1
            if recorder:
                recorder.check()
            if (run / "stop-request.json").exists():
                controller.stop("user_requested")
                break
            if status["status"] not in {"queued", "preparing", "running"}:
                break
            if status["status"] == "queued" and time.monotonic() - started >= args.queue_timeout:
                controller.stop("queue_wait_timeout")
                break
            time.sleep(0.1)
        else:
            controller.stop("deployment_deadline")
        status = controller.status()
        write_json(run / "controller-status.json", {k: v for k, v in status.items() if k != "lease_id"})
        write_json(run / "controller-terminal.json", {k: v for k, v in status.items() if k != "lease_id"})
        result = {
            "state": status["status"],
            "error": status.get("error"),
            "worker_finished": provider.finished,
            "simulation": simulation,
            "packets_submitted": status.get("packets_submitted"),
            "task_success": None,
            "physical_execution_verified": False,
            "motion_sent": not simulation and bool(status.get("packets_submitted")),
            "session_id": status.get("session_id"),
            "episode_id": status.get("episode_id"),
        }
        write_json(run / "deployment.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if provider.finished and not status.get("error") and not provider.failure else 1
    finally:
        final_status = controller.status()
        if final_status.get("session_id"):
            try:
                write_json(run / "robot-trace.json", controller.sanitized_episode_trace())
            except Exception as exc:
                write_json(run / "robot-trace-error.json", {"type": type(exc).__name__, "error": str(exc)})
        controller.disconnect("deployment_exit")
        provider.close()
        if not simulation and final_status.get("session_id"):
            parking_deadline = time.monotonic() + 90
            while time.monotonic() < parking_deadline:
                try:
                    observation = api.get_robot_observation(args.robot_id)
                    queue = api.get_queue_snapshot()
                    row = {"sampled_at": time.time(), "observation": observation, "queue": queue}
                    append_jsonl(run / "parking.jsonl", row)
                    if observation.get("mode") == "DISABLED" and not queue.get("entries"):
                        write_json(run / "parking-final.json", row)
                        break
                except Exception as exc:
                    append_jsonl(run / "parking.jsonl", {"error": str(exc), "sampled_at": time.time()})
                time.sleep(3)
        if recorder:
            # HLS is delayed: keep the capture open briefly after parking.
            time.sleep(12)
            recorder.close()
        knowledge.verify(run)


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest="command", required=True)
    for command in ("prepare", "probe", "smoke", "run"):
        p = subs.add_parser(command)
        p.add_argument("--run-dir", type=Path, required=True)
        if command != "probe":
            p.add_argument("--knowledge-source", type=Path, required=True)
            p.add_argument("--knowledge-task", action="append", help="Default: all frozen tasks")
        if command != "prepare":
            p.add_argument("--session-api", default=upstream.SESSION_API)
            p.add_argument("--robot-id", default="yam-1")
        if command in {"smoke", "run"}:
            p.add_argument("--model", default="gpt-6-astra")
            p.add_argument("--reasoning-effort", default="xhigh")
            p.add_argument("--image", default="embodiedevo-agent:blupe")
            p.add_argument("--duration", type=int, default=300)
            p.add_argument("--queue-timeout", type=int, default=300)
        if command == "run":
            p.add_argument("--video-url", help="Native HLS video to record before admission until after parking")
            p.add_argument(
                "--instruction", required=True, help="Actual physical task; joins the queue"
            )
        if command == "smoke":
            p.add_argument(
                "--container",
                action="store_true",
                help="Run the offline client inside the isolated agent image; no model",
            )
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command in {"smoke", "run"}:
        if not 60 <= args.duration <= 600 or args.queue_timeout <= 0:
            raise ValueError("Duration must be 60–600 seconds; queue timeout must be positive")
        if args.command == "smoke" and args.robot_id != "yam-1":
            raise ValueError("The offline smoke fixture represents yam-1 only")
        if args.command == "run" and not 1 <= len(args.instruction.strip()) <= 4000:
            raise ValueError("Provide a task instruction of 1–4000 characters")
    run = knowledge.new_run(args.run_dir)
    try:
        if args.command == "probe":
            result = probe(args.session_api, args.robot_id, run / "remote")
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in (
                            "service_reachable",
                            "queue_ready",
                            "queue_blockers",
                            "motion_ready",
                            "motion_blockers",
                            "cameras",
                            "monitor_websocket",
                            "motion_sent",
                        )
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if result["queue_ready"] else 2
        info = knowledge.prepare(args.knowledge_source, run, args.knowledge_task)
        print(
            json.dumps(
                {"knowledge": info["counts"], "tasks": len(info["tasks"]), "run_dir": str(run)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        if args.command == "prepare":
            return 0
        return execute(run, args, simulation=args.command == "smoke")
    except BaseException as exc:
        write_json(run / "failure.json", {"type": type(exc).__name__, "error": str(exc)})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
