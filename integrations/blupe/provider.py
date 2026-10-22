"""A code-program provider for the pinned RunnerController packet lifecycle."""

import contextlib
import copy
import io
import multiprocessing
import time

import numpy as np
from PIL import Image
from remote_yam.providers import PolicyComplete

from integrations.blupe.actions import YamPlanner


class EmbodiedEvoProvider:
    provider_name = "embodiedevo"
    share_conversation = False

    def __init__(
        self,
        *,
        run,
        instruction,
        robot_id,
        calibration,
        camera_source,
        settings,
        worker_target=None,
    ):
        from integrations.blupe.worker import run_worker

        self.run, self.instruction, self.robot_id = run, instruction, robot_id
        self.settings, self.model = settings, settings["model"]
        self.planner = YamPlanner(robot_id, calibration)
        self.camera_source = camera_source
        self.worker_target = worker_target or run_worker
        self.cancelled = lambda: False
        self.refresh_observation = None
        self.interaction_sink = None
        self.pending = self.completed = self.identity = self.feedback = None
        self.process = self.connection = None
        self.finished = False
        self.failure = None
        self.simulation = settings.get("simulation", False)

    def public_config(self):
        return {
            "provider": self.provider_name,
            "model": self.model,
            "phase": "finished" if self.finished else "frozen_knowledge_execution",
            "knowledge_read_only": True,
            "task_success": None,
            "controller_workspace": self.planner.profile.summary(),
        }

    def _check(self):
        if self.failure or self.cancelled():
            raise RuntimeError(self.failure or "EmbodiedEvo execution cancelled")

    def _fresh(self):
        self._check()
        obs = self.feedback
        if time.time() - obs.get("observed_at", 0) > 2:
            if not callable(self.refresh_observation):
                raise RuntimeError("Fresh YAM feedback is unavailable")
            obs = self.refresh_observation()
        if (
            tuple(obs.get(k) for k in ("episode_id", "lease_id")) != self.identity
            or obs.get("step_id") != self.feedback["step_id"]
            or obs.get("jetson_id") != self.robot_id
            or obs.get("source") != ("simulation" if self.simulation else "hardware")
            or obs.get("settled") is not True
            or not -2 <= time.time() - obs.get("observed_at", 0) <= 10
        ):
            raise RuntimeError("YAM observation changed identity, cursor, source or freshness")
        if not self.simulation:
            from integrations.blupe.probe import readiness

            # Home is required at the first leased handoff, not before joining the queue.
            reasons = [r for r in readiness(obs) if r != "not_homed" or obs["step_id"] == 0]
            if reasons:
                raise RuntimeError("YAM feedback blocked: " + ", ".join(reasons))
        self.feedback = copy.deepcopy(obs)
        return self.feedback

    def _observation(self):
        obs = self._fresh()
        frames = self.camera_source.capture_for_policy(obs, cancelled=self.cancelled)
        vision = {}
        names = {"top": "cam_head", "left": "cam_left_wrist", "right": "cam_right_wrist"}
        for frame in frames:
            if frame.name not in names:
                raise RuntimeError("Unexpected YAM camera")
            stamp = frame.source_captured_at
            if not self.simulation and (stamp is None or not -2 <= time.time() - stamp <= 10):
                raise RuntimeError("Camera timestamp missing or stale")
            if stamp is not None and stamp < obs.get("settled_after", 0):
                raise RuntimeError("Camera precedes the settled movement feedback")
            with Image.open(io.BytesIO(frame.jpeg)) as image:
                rgb = np.array(image.convert("RGB"))
            vision[names[frame.name]] = {
                "color": rgb,
                "captured_at": stamp,
                "received_at": frame.received_at,
            }
        if len(vision) != 3:
            raise RuntimeError("All three YAM views are required")
        state = self.planner.state(obs)
        return {
            "instruction": self.instruction,
            "vision": vision,
            "state": state,
            "action": {k: v.copy() for k, v in state.items() if k.endswith("joint_state")},
            "additional_info": {
                "frequency": 10,
                "frame": "yam_model_base_midpoint",
                "ee_pose_source": "FK_from_measured_joints",
                "observed_at": obs["observed_at"],
                "success_available": False,
            },
        }

    def build_trajectory(self, prompt, observation, first_step_id):
        self._check()
        if prompt != self.instruction:
            raise ValueError("Task instruction changed during deployment")
        identity = tuple(observation.get(k) for k in ("episode_id", "lease_id"))
        if not all(identity) or (self.identity is not None and identity != self.identity):
            raise RuntimeError("A new episode/lease requires a fresh EmbodiedEvo deployment")
        if observation.get("step_id") != first_step_id or self.pending is not None:
            raise RuntimeError("Uncorrelated packet cursor or incomplete movement")
        self.identity, self.feedback = identity, copy.deepcopy(observation)
        self._fresh()
        if self.process is None:
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(
                target=self.worker_target,
                args=(child, str(self.run), self.instruction, self.settings),
            )
            self.process.start()
            child.close()
        if self.completed is not None:
            count, cursor = self.completed
            if first_step_id != cursor:
                raise RuntimeError("Completion cursor changed before worker resume")
            self.connection.send(
                {"ok": True, "observation": self._observation(), "waypoint_count": count}
            )
            self.completed = None
        while True:
            self._check()
            if not self.connection.poll(0.05):
                if not self.process.is_alive():
                    raise RuntimeError("EmbodiedEvo worker exited without a completion record")
                continue
            message = self.connection.recv()
            op = message.get("op")
            if op == "finished":
                self.finished = True
                if message.get("valid") is not True:
                    self.failure = "EmbodiedEvo worker failed; see preserved worker evidence"
                    raise RuntimeError(self.failure)
                raise PolicyComplete("EmbodiedEvo finished; task success remains unverified")
            if op == "observe":
                self.connection.send({"ok": True, "observation": self._observation()})
            elif op == "step":
                obs = self._fresh()
                try:
                    points = self.planner.build(message.get("action"), obs, first_step_id)
                except ValueError as exc:
                    self.connection.send({"ok": False, "recoverable": True, "error": str(exc)})
                    continue
                self.pending = (len(points), points[-1]["step_id"] + 1)
                return points
            else:
                raise RuntimeError("Unknown host bridge operation")

    def trajectory_completed(self, observation, waypoint_count):
        expected = (waypoint_count, observation.get("step_id"))
        if self.pending != expected or observation.get("settled") is not True:
            raise RuntimeError("YAM completion does not match the pending program packet")
        self.completed, self.pending = self.pending, None

    def trajectory_failed(self, code, message, waypoint_count, *, feedback=None):
        self.failure = f"YAM packet failed: {code}; partial execution is possible"
        if self.connection is not None:
            self.connection.send({"ok": False, "recoverable": False, "error": self.failure})

    def close(self):
        self.failure = self.failure or "Deployment closed"
        if self.connection is not None:
            with contextlib.suppress(OSError, EOFError):
                self.connection.send({"ok": False, "error": self.failure})
        if self.process is not None:
            self.process.join(3)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(3)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(3)
        if self.connection is not None:
            self.connection.close()
