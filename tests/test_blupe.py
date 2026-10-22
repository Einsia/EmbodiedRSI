"""Frozen knowledge and remote execution boundaries; no model, network or hardware."""

import copy
import hashlib
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from integrations.blupe import knowledge, probe, upstream
from integrations.blupe.environment import BluPeApi, BluPeTask, PipeBridge
from integrations.blupe.worker import agent_command
from orchestrator.session import Session
from simulators.primitives import PROGRAM_PRIMITIVES
from tests.fixtures import observation


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.files = {
            "skills/helper.py": b"def hold():\n    return get_observation()\n",
            "lessons/grasp.md": b"Check the object after lifting.\n",
        }
        for name, content in self.files.items():
            kind, tail = name.split("/", 1)
            path = self.source / kind / "pick" / tail
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
        (self.source / "experience-manifest.json").write_text(
            json.dumps(
                {
                    "tasks": {
                        "pick": {
                            "files": {
                                name: hashlib.sha256(b).hexdigest()
                                for name, b in self.files.items()
                            }
                        }
                    }
                }
            )
        )
        (self.source / "recipes").mkdir()
        (self.source / "recipes" / "private.md").write_text("Do not transfer")
        (self.source / "submission").mkdir()
        (self.source / "submission" / "solution.py").write_text("Do not inherit")

    def test_only_verified_knowledge_and_fresh_program_transfer(self):
        run = knowledge.new_run(self.root / "202610160001_prepare")
        record = knowledge.prepare(self.source, run)
        self.assertEqual(record["counts"], {"skills": 1, "lessons": 1})
        self.assertFalse((run / "workspace/recipes").exists())
        self.assertNotIn("inherit", (run / "workspace/submission/solution.py").read_text())
        self.assertEqual(
            (run / "workspace/skills/pick/helper.py").read_bytes(), self.files["skills/helper.py"]
        )
        self.assertEqual(knowledge.verify(run), record)
        with self.assertRaises(FileExistsError):
            knowledge.new_run(run)
        helper = run / "workspace/skills/pick/helper.py"
        helper.chmod(0o644)
        helper.write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            knowledge.verify(run)

    def test_tampered_export_rejected_before_workspace_creation(self):
        (self.source / "skills/pick/helper.py").write_text("tampered")
        run = knowledge.new_run(self.root / "202610160002_prepare")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            knowledge.prepare(self.source, run)
        self.assertFalse((run / "workspace").exists())

    def test_symlink_cannot_import_external_contents(self):
        file = self.source / "skills/pick/helper.py"
        other = self.root / "external"
        other.write_bytes(file.read_bytes())
        file.unlink()
        file.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            knowledge.inventory(self.source)

    def test_traversal_and_unknown_task_rejected(self):
        with self.assertRaises(ValueError):
            knowledge.inventory(self.source, ["../pick"])
        manifest = self.source / "experience-manifest.json"
        data = json.loads(manifest.read_text())
        data["tasks"]["pick"]["files"]["skills/../../private"] = "0" * 64
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            knowledge.inventory(self.source)


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Mock()
        self.bridge.call.return_value = {"observation": observation(), "waypoint_count": 7}
        self.env = BluPeTask(self.bridge, "Pick the object")
        self.env.reset(seed=0)

    def test_persistent_namespace_and_waypoint_budget(self):
        first = self.env.step("marker = 41\nstep({})")
        second = self.env.step("assert marker == 41\nmarker += 1")
        self.assertTrue(first[4]["ok"])
        self.assertTrue(second[4]["ok"])
        self.assertEqual(self.env.control_steps, 7)
        self.assertIsNone(self.env.transition()[4]["success"])
        self.assertEqual(set(BluPeApi(self.env).functions()), PROGRAM_PRIMITIVES)

    def test_reset_alias_never_reaches_remote(self):
        count = self.bridge.call.call_count
        result = self.env.step("again = reset\nagain()")
        self.assertFalse(result[4]["ok"])
        self.assertIn("PermissionError", result[4]["stderr"])
        self.assertEqual(self.bridge.call.call_count, count)

    def test_uncertain_step_poisoned_even_if_program_catches_error(self):
        self.bridge.call.side_effect = RuntimeError("uncertain dispatch")
        self.env.step("try:\n    step({})\nexcept Exception:\n    pass")
        self.assertTrue(self.env.truncated)
        count = self.bridge.call.call_count
        with self.assertRaises(RuntimeError):
            self.env.control_step({})
        self.assertEqual(self.bridge.call.call_count, count)

    def test_pre_dispatch_validation_error_allows_correction(self):
        self.bridge.call.side_effect = ValueError("invalid target")
        self.assertFalse(self.env.step("step({})")[4]["ok"])
        self.assertFalse(self.env.truncated)
        self.assertEqual(self.env.control_steps, 0)

    def test_pipe_timeout_never_retries(self):
        connection = Mock()
        connection.poll.return_value = False
        with self.assertRaisesRegex(RuntimeError, "do not replay"):
            PipeBridge(connection, 0.01).call("step", action={})
        connection.send.assert_called_once()

    def test_no_evaluator_stays_unknown_in_public_and_private_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            workspace = run / "workspace"
            for name in ("observations", "submission", "scratch", "skills", "lessons"):
                (workspace / name).mkdir(parents=True)
            (workspace / "instruction.md").write_text("Frozen deployment")
            env = BluPeTask(self.bridge, "task")
            session = Session(
                env, run, phase="test", seed=0, budget=100, seconds=60, execution_seconds=10
            )
            with patch("orchestrator.session.write_videos", return_value={}):
                result = session.handle(
                    {"op": "exec", "files": [{"path": "submission/a.py", "code": "value = 1"}]}
                )
            session.handle({"op": "finish"})
            self.assertIsNone(result["success"])
            self.assertIsNone(
                json.loads((run / "session-result.json").read_text())["task_completed"]
            )
            ledger = json.loads((run / "ledger.jsonl").read_text())
            self.assertIsNone(ledger["success"])
            self.assertIsNone(ledger["reward"])

    def test_container_mounts_all_knowledge_readonly_and_no_robot_source(self):
        settings = {"model": "test-model", "reasoning_effort": "high", "image": "agent:test"}
        command = agent_command(
            Path("/tmp/run"), Path("/tmp/control"), Path("/tmp/home"), settings, "test-container"
        )
        for component in ("skills", "lessons", "primitives", "observations", "scripts"):
            self.assertIn(
                f"type=bind,src=/tmp/run/workspace/{component},dst=/workspace/{component},readonly",
                command,
            )
        self.assertNotIn("--gpus", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))
        self.assertNotIn(str(upstream.CHECKOUT), " ".join(command))


class AvailabilityTests(unittest.TestCase):
    @staticmethod
    def parked_feedback():
        observation = {
            "jetson_id": "yam-1",
            "source": "hardware",
            "observed_at": 100.0,
            "mode": "DISABLED",
            "queue_ready": True,
            "homed": False,
            "settled": False,
            "safety": {"ok": False, "estop_engaged": False, "reason": "hardware_not_initialized"},
            "left_gripper": None,
            "right_gripper": None,
        }
        station = {**observation, "connected": True, "available": False}
        return observation, station

    def test_parked_auto_queue_station_can_join_but_cannot_move_yet(self):
        observation, station = self.parked_feedback()
        self.assertEqual(probe.queue_readiness(observation, station, "yam-1", now=100.0), [])
        self.assertIn("controller_DISABLED", probe.readiness(observation, now=100.0))
        self.assertIn("not_homed", probe.readiness(observation, now=100.0))
        self.assertIn("left_gripper_unavailable", probe.readiness(observation, now=100.0))

    def test_queue_ready_does_not_override_fault_estop_disconnect_or_stale_state(self):
        cases = [
            ({}, {"queue_ready": False}),
            ({"queue_ready": False}, {}),
            ({}, {"connected": False}),
            ({"mode": "FAULT"}, {}),
            ({}, {"mode": "FAULT"}),
            ({"observed_at": 80.0}, {}),
            ({}, {"observed_at": 80.0}),
            ({"jetson_id": "other-robot"}, {}),
            ({"safety": {"ok": True, "estop_engaged": True}}, {}),
            ({"safety": {"ok": True}}, {}),
            ({"safety": {"ok": False, "estop_engaged": False, "reason": "joint_limit"}}, {}),
            ({"safety": {"ok": True, "estop_engaged": False, "contact_count": 1}}, {}),
        ]
        for observation_update, station_update in cases:
            with self.subTest(observation=observation_update, station=station_update):
                observation, station = self.parked_feedback()
                observation.update(observation_update)
                station.update(station_update)
                self.assertTrue(probe.queue_readiness(observation, station, "yam-1", now=100.0))

    def test_health_alone_does_not_prove_hardware_ready(self):
        obs = {
            "source": "hardware",
            "observed_at": 100.0,
            "mode": "DISABLED",
            "homed": False,
            "settled": False,
            "safety": {"ok": False, "estop_engaged": False, "reason": "hardware_not_initialized"},
            "left_joints_deg": [0.0] * 6,
            "right_joints_deg": [0.0] * 6,
            "left_gripper": None,
            "right_gripper": None,
        }
        reasons = probe.readiness(obs, now=100.0)
        self.assertIn("controller_DISABLED", reasons)
        self.assertIn("left_gripper_unavailable", reasons)
        self.assertIn("safety_hardware_not_initialized", reasons)

    def test_stale_nan_and_boolean_measurements_cannot_pass(self):
        obs = {
            "source": "hardware",
            "observed_at": 99.0,
            "mode": "READY",
            "homed": True,
            "settled": True,
            "safety": {"ok": True, "estop_engaged": False},
            "left_joints_deg": [0.0] * 6,
            "right_joints_deg": [0.0] * 6,
            "left_gripper": 0.5,
            "right_gripper": 0.5,
        }
        self.assertEqual(probe.readiness(obs, now=100.0), [])
        self.assertIn("stale_or_missing_observation", probe.readiness(obs, now=120.0))
        obs["left_gripper"] = True
        obs["right_joints_deg"][0] = float("nan")
        self.assertIn("left_gripper_unavailable", probe.readiness(obs, now=100.0))
        self.assertIn("right_joints_unavailable", probe.readiness(obs, now=100.0))

    def test_probe_read_uses_only_get_and_rejects_credential_urls(self):
        response = Mock()
        response.status, response.headers = 200, {}
        response.read.return_value = b'{"ok":true}'
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch.object(probe.request, "build_opener") as factory:
            factory.return_value.open.return_value = context
            self.assertTrue(probe.read("https://example.test", "/health")["ok"])
            req = factory.return_value.open.call_args.args[0]
            self.assertEqual(req.method, "GET")
            self.assertIsNone(req.data)
        with self.assertRaises(ValueError):
            probe.validate_origin("https://secret@example.test")


class NativePlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(importlib.util.find_spec(p) for p in ("mujoco", "scipy", "websocket")):
            raise unittest.SkipTest("Optional BluPe CPU dependencies are not installed")
        if not upstream.CHECKOUT.exists():
            raise unittest.SkipTest("Pinned BluPe checkout is not provisioned")
        upstream.load()

    def setUp(self):
        from remote_yam.session import MockSessionAPI

        from integrations.blupe.actions import YamPlanner

        self.planner = YamPlanner("yam-1", {"robot_id": "yam-1"})
        self.obs = copy.deepcopy(MockSessionAPI.DEFAULT_OBSERVATIONS[0])
        self.obs.update(
            observed_at=time.time(), settled=True, source="simulation", jetson_id="yam-1"
        )
        self.state = self.planner.state(self.obs)

    def test_joint_units_pacing_and_independent_other_arm(self):
        action = {k: v.copy() for k, v in self.state.items() if k.endswith("joint_state")}
        action["left_arm_joint_state"][0] += 0.07
        points = self.planner.build(action, self.obs, 0)
        self.assertAlmostEqual(points[-1]["left_joints_deg"][0], np.rad2deg(0.07))
        self.assertEqual(points[-1]["right_joints_deg"], self.obs["right_joints_deg"])
        self.assertEqual([p["step_id"] for p in points], list(range(len(points))))
        q = np.deg2rad([self.obs["left_joints_deg"], *[p["left_joints_deg"] for p in points]])
        self.assertLessEqual(float(np.abs(np.diff(q, axis=0)).max()), 0.035 + 1e-9)

    def test_pose_target_is_reached_in_yam_frame(self):
        action = {k: v.copy() for k, v in self.state.items() if "arm_joint" not in k}
        action["left_ee_pose"][2] += 0.002
        points = self.planner.build(action, self.obs, 7)
        final = {**self.obs, **points[-1]}
        measured = self.planner.state(final)
        np.testing.assert_allclose(
            measured["left_ee_pose"][:3], action["left_ee_pose"][:3], atol=2e-6
        )
        np.testing.assert_allclose(
            measured["right_ee_pose"][:3], self.state["right_ee_pose"][:3], atol=2e-6
        )

    def test_unreachable_malformed_and_exhausted_targets_never_produce_packet(self):
        action = {k: v.copy() for k, v in self.state.items() if "arm_joint" not in k}
        for key, value in (
            ("left_ee_pose", [100.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
            ("left_ee_pose", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            ("left_ee_joint_state", [float("nan")]),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.planner.build({**action, key: value}, self.obs, 0)
        with self.assertRaises(ValueError):
            self.planner.build(action, self.obs, 3000)
        with self.assertRaises(ValueError):
            self.planner.build({**action, 7: [0]}, self.obs, 0)
        del action["right_ee_pose"]
        with self.assertRaises(ValueError):
            self.planner.build(action, self.obs, 0)

    def test_receipt_cannot_resume_program_until_correlated_settled_completion(self):
        from integrations.blupe.provider import EmbodiedEvoProvider

        provider = object.__new__(EmbodiedEvoProvider)
        provider.pending, provider.completed = (3, 10), None
        for feedback in ({"step_id": 9, "settled": True}, {"step_id": 10, "settled": False}):
            with self.assertRaises(RuntimeError):
                provider.trajectory_completed(feedback, 3)
        self.assertIsNone(provider.completed)
        provider.trajectory_completed({"step_id": 10, "settled": True}, 3)
        self.assertEqual(provider.completed, (3, 10))
        self.assertIsNone(provider.pending)

    def test_queue_not_ready_cannot_create_a_session_or_start_a_worker(self):
        from integrations.blupe import run as runner

        args = SimpleNamespace(session_api="https://example.test", robot_id="yam-1")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                runner,
                "probe",
                return_value={"queue_ready": False, "queue_blockers": ["station_not_queue_ready"]},
            ),
            patch("remote_yam.session.HttpSessionAPI") as api,
            patch("remote_yam.controller.RunnerController") as controller,
            patch("integrations.blupe.provider.EmbodiedEvoProvider") as provider,
        ):
            run = Path(temporary)
            self.assertEqual(runner.execute(run, args, simulation=False), 2)
            result = json.loads((run / "deployment.json").read_text())
            self.assertEqual(result["state"], "blocked_before_queue")
            self.assertFalse(result["motion_sent"])
            self.assertIsNone(result["task_success"])
            api.assert_not_called()
            controller.assert_not_called()
            provider.assert_not_called()

    def test_parked_queue_ready_state_reaches_controller_join(self):
        from integrations.blupe import run as runner

        args = SimpleNamespace(
            session_api="https://example.test",
            robot_id="yam-1",
            image="test:agent",
            model="test",
            reasoning_effort="high",
            duration=60,
            queue_timeout=1,
            instruction="Observe only",
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(
                runner,
                "probe",
                return_value={
                    "queue_ready": True,
                    "motion_ready": False,
                    "checks": {"calibration": {"data": {"robot_id": "yam-1"}}},
                },
            ),
            patch("remote_yam.session.HttpSessionAPI"),
            patch("remote_yam.controller.RunnerController") as controller,
            patch("integrations.blupe.provider.EmbodiedEvoProvider") as provider,
            patch("orchestrator.launch_agent.require_image"),
            patch("orchestrator.launch_agent.ensure_network"),
            patch("orchestrator.launch_agent.agent_codex_home"),
            patch.object(knowledge, "verify"),
        ):
            provider.return_value.finished = True
            provider.return_value.failure = None
            controller.return_value.status.return_value = {
                "status": "stopped",
                "error": None,
                "packets_submitted": 0,
            }
            self.assertEqual(runner.execute(Path(temporary), args, simulation=False), 0)
            controller.return_value.join_and_run.assert_called_once_with(
                provider.return_value, "Observe only", run_duration_s=60
            )

    def test_first_leased_handoff_still_requires_home_and_measured_grippers(self):
        from integrations.blupe.provider import EmbodiedEvoProvider

        provider = object.__new__(EmbodiedEvoProvider)
        provider.failure = None
        provider.cancelled = lambda: False
        provider.identity = ("episode", "lease")
        provider.robot_id = "yam-1"
        provider.simulation = False
        provider.feedback = {
            **self.obs,
            "episode_id": "episode",
            "lease_id": "lease",
            "step_id": 0,
            "source": "hardware",
            "mode": "READY",
            "homed": False,
            "safety": {"ok": True, "estop_engaged": False},
        }
        with self.assertRaisesRegex(RuntimeError, "not_homed"):
            provider._fresh()
        provider.feedback["homed"] = True
        provider.feedback["left_gripper"] = None
        with self.assertRaisesRegex(RuntimeError, "left_gripper_unavailable"):
            provider._fresh()
        provider.feedback["left_gripper"] = 0.5
        self.assertIsNotNone(provider._fresh())
        provider.feedback.update(step_id=1, homed=False)
        self.assertIsNotNone(provider._fresh())


if __name__ == "__main__":
    unittest.main()
