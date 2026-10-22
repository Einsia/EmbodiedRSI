"""Offline-only extension of upstream's deterministic session mock."""

import time
from copy import deepcopy

from remote_yam.session import MockSessionAPI


class ObservableMock(MockSessionAPI):
    """Expose the mock's latest settled cursor to the upstream refresh callback."""

    def _event(self, record, event_type, payload):
        if event_type == "observation":
            record["measured"] = deepcopy(payload)
        super()._event(record, event_type, payload)

    def get_session(self, session_id):
        with self._lock:
            state = super().get_session(session_id)
            record = self._record(session_id)
            state.update(
                lease_id=record["lease_id"],
                latest_observation_step=record.get("measured", {}).get("step_id"),
                active_trajectory=deepcopy(record["active_trajectory"]),
            )
            return state

    def get_robot_observation(self, robot_id):
        with self._lock:
            for record in self._sessions.values():
                if record["status"] == "running" and "measured" in record:
                    obs = deepcopy(record["measured"])
                    obs.update(jetson_id=robot_id, observed_at=time.time())
                    return obs
            return super().get_robot_observation(robot_id)
