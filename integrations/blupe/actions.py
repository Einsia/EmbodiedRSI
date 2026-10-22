"""RoboDojo-shaped helpers on YAM: explicit frames, measured FK and bounded packets."""

import math

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


def vector(value, size, label):
    data = np.asarray(value)
    if data.shape != (size,) or data.dtype.kind not in "fiu" or not np.isfinite(data).all():
        raise ValueError(f"{label} requires {size} finite numbers")
    return data.astype(float)


class YamPlanner:
    """Use the pinned runner's kinematics and station profiles, never RoboDojo geometry."""

    def __init__(self, robot_id, calibration):
        from remote_yam.controller_workspace import workspace_for_robot
        from remote_yam.mujoco_ik import BimanualRelativeIK

        self.profile = workspace_for_robot(robot_id)
        if self.profile is None:
            raise ValueError("Only a YAM station with a reviewed controller workspace is supported")
        self.profile.validate_report(calibration)
        self.ik = BimanualRelativeIK(**self.profile.rgb_geometry_kwargs())
        self.bases = [np.array([0.0, sign * self.ik.base_spacing_m / 2, 0.0]) for sign in (1, -1)]

    def measured(self, observation):
        q = np.concatenate(
            [vector(observation.get(a + "_joints_deg"), 6, a) for a in ("left", "right")]
        )
        self.ik._safe_baseline(q[:6], q[6:])
        q = np.deg2rad(q)
        grips = []
        for arm in ("left", "right"):
            value = observation.get(arm + "_gripper")
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Measured gripper feedback missing: " + arm)
            grips.append(value)
        self.ik._forward(q)
        return q, np.asarray(grips), self.ik._poses()

    def state(self, observation):
        q, grips, poses = self.measured(observation)
        state = {}
        for i, arm in enumerate(("left", "right")):
            p, r = poses[i]
            quat = Rotation.from_matrix(r).as_quat(scalar_first=True)
            state[arm + "_arm_joint_state"] = q[i * 6 : i * 6 + 6].copy()
            state[arm + "_ee_joint_state"] = np.array([grips[i]])
            state[arm + "_ee_pose"] = np.r_[p, quat]
        return state

    def build(self, action, observation, first_step_id):
        from remote_yam.session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET

        if not isinstance(action, dict) or not all(isinstance(k, str) for k in action):
            raise ValueError("step(action) requires a complete dictionary")
        ee = any(k.endswith("ee_pose") for k in action)
        keys = {
            a + k
            for a in ("left", "right")
            for k in ("_ee_joint_state", "_ee_pose" if ee else "_arm_joint_state")
        }
        if set(action) != keys:
            raise ValueError("Both arms and grippers are required; do not mix joint and EE modes")
        start_q, start_g, start_poses = self.measured(observation)
        if self.ik.data.ncon:
            raise ValueError("Observed baseline has predictive YAM model contacts")
        goal_g = np.array(
            [vector(action[a + "_ee_joint_state"], 1, a + " gripper")[0] for a in ("left", "right")]
        )
        if np.any(goal_g < 0) or np.any(goal_g > 1):
            raise ValueError("Gripper targets must be in [0, 1]")
        count = max(1, math.ceil(float(np.max(np.abs(goal_g - start_g))) / 0.1))
        if ee:
            goals, rotations = [], []
            for i, arm in enumerate(("left", "right")):
                pose = vector(action[arm + "_ee_pose"], 7, arm + " pose")
                if abs(float(np.linalg.norm(pose[3:])) - 1) > 1e-4:
                    raise ValueError("Quaternion must be unit length, in wxyz order")
                low, high = self.profile.bounds(arm)
                relative = pose[:3] - self.bases[i]
                if np.any(relative < low) or np.any(relative > high):
                    raise ValueError(arm + " target outside saved controller workspace")
                goal_r = Rotation.from_quat(pose[3:], scalar_first=True)
                start_r = Rotation.from_matrix(start_poses[i][1])
                rotations.append(Slerp([0, 1], Rotation.concatenate([start_r, goal_r])))
                goals.append(pose[:3])
                count = max(
                    count,
                    math.ceil(np.linalg.norm(pose[:3] - start_poses[i][0]) / 0.005),
                    math.ceil((goal_r * start_r.inv()).magnitude() / 0.025),
                )
        else:
            goal_q = np.concatenate(
                [
                    vector(action[a + "_arm_joint_state"], 6, a + " joints")
                    for a in ("left", "right")
                ]
            )
            if np.any(goal_q < self.ik.low) or np.any(goal_q > self.ik.high):
                raise ValueError("Joint target outside YAM limits")
            count = max(count, math.ceil(float(np.max(np.abs(goal_q - start_q))) / 0.035))
        limit = min(MAX_WAYPOINTS_PER_PACKET, MAX_COMMANDS - first_step_id)
        points = []

        def extend(a, b, previous, depth=0):
            if ee:
                targets = tuple(
                    (p + b * (g - p), r(b).as_matrix())
                    for (p, _), g, r in zip(start_poses, goals, rotations, strict=True)
                )
                try:
                    q = self.ik._solve(previous, targets)
                except RuntimeError as exc:
                    raise ValueError("YAM Cartesian path cannot be solved safely") from exc
            else:
                q = start_q + b * (goal_q - start_q)
            if not np.isfinite(q).all() or np.any(q < self.ik.low) or np.any(q > self.ik.high):
                raise ValueError("Planned waypoint outside YAM joint limits")
            if np.max(np.abs(q - previous)) > 0.035 + 1e-10:
                if depth >= 10:
                    raise ValueError("Path cannot meet YAM joint pacing")
                mid = (a + b) / 2
                return extend(mid, b, extend(a, mid, previous, depth + 1), depth + 1)
            if len(points) >= limit:
                raise ValueError("Waypoint budget exceeded; request a smaller move")
            self.ik._forward(q)
            if self.ik.data.ncon:
                raise ValueError("Predictive YAM model contact detected")
            for i, arm in enumerate(("left", "right")):
                p = self.ik._poses()[i][0] - self.bases[i]
                low, high = self.profile.bounds(arm)
                if np.any(p < low - 1e-6) or np.any(p > high + 1e-6):
                    raise ValueError("Path leaves the saved controller workspace")
            grips = start_g + b * (goal_g - start_g)
            degrees = np.rad2deg(q).reshape(2, 6)
            points.append(
                dict(
                    step_id=first_step_id + len(points),
                    left_joints_deg=degrees[0].tolist(),
                    right_joints_deg=degrees[1].tolist(),
                    left_gripper=float(grips[0]),
                    right_gripper=float(grips[1]),
                )
            )
            return q

        if count > limit:
            raise ValueError("Waypoint budget exceeded; request a smaller move")
        previous = start_q
        for index in range(count):
            previous = extend(index / count, (index + 1) / count, previous)
        return points
