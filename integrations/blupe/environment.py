"""Persistent Python environment with the existing four primitive names."""

import copy

import numpy as np

from simulators.execution import CodeTask


class BluPeApi:
    def __init__(self, env):
        self.env = env

    def functions(self):
        return {
            "get_instruction": self.env.task_prompt,
            "get_observation": self.env.refresh,
            "step": self.env.control_step,
            "reset": self.reset,
        }

    def reset(self):
        raise PermissionError("Frozen YAM deployment forbids reset; Home does not restore objects")


class PipeBridge:
    def __init__(self, connection, timeout):
        self.connection, self.timeout = connection, timeout

    def call(self, op, **arguments):
        # Only trusted host processes share this pipe; it is never mounted in the agent.
        self.connection.send({"op": op, **arguments})
        if not self.connection.poll(self.timeout):
            raise RuntimeError("YAM feedback timeout; execution may have occurred, do not replay")
        response = self.connection.recv()
        if response.get("ok") is not True:
            kind = ValueError if response.get("recoverable") else RuntimeError
            raise kind(response.get("error", "YAM controller stopped"))
        return response


class BluPeTask(CodeTask):
    success_known = False
    native_feedback_size = True

    def __init__(self, bridge, instruction, *, limit=3000):
        super().__init__()
        self.bridge = bridge
        self._task_prompt = instruction
        self.max_steps = self.native_step_limit = limit
        self.initialize(BluPeApi(self))
        # Initialize NumPy formatting before the restricted snippet import hook runs.
        str(np.zeros(1))
        repr(np.zeros(1))

    def reset(self, *, seed=None, options=None):
        if self._observation is not None:
            raise PermissionError("YAM deployment cannot reset a physical scene")
        result = self.bridge.call("observe")
        return self.start_episode(seed, result["observation"])

    def reset_from_program(self):
        raise PermissionError("reset() is forbidden in frozen YAM deployment")

    def refresh(self):
        if not self.done:
            try:
                self.observe(self.bridge.call("observe")["observation"])
            except Exception:
                self.truncated = True
                raise
        return self.public_observation()

    def control_step(self, action):
        if self.done:
            raise RuntimeError("YAM episode stopped; no further motion is permitted")
        try:
            # The privileged parent performs all validation, kinematics and dispatch.
            result = self.bridge.call("step", action=copy.deepcopy(action))
        except ValueError:
            raise  # Planning rejection before dispatch: let the agent correct its code.
        except Exception:
            self.truncated = True
            raise
        self.control_steps += result["waypoint_count"]
        self.truncated = self.control_steps >= self.max_steps
        self.observe(result["observation"])
        return self.transition()

    def transition(self, reward=0.0):
        return (
            self.public_observation(),
            0.0,
            self.terminated,
            self.truncated,
            {
                "success": None,
                "success_source": "unavailable",
                "reward_available": False,
                "native_steps": self.control_steps,
            },
        )
