"""Shared stuck state between FollowPath and StuckDetectionUtility."""
import time

_stuck_state = {
    "followpath_retries": 0,
    "followpath_stuck_count": 0,
    "followpath_active": False,
    "last_update": 0.0,
}


def update_followpath_state(retries, stuck_count, active):
    _stuck_state["followpath_retries"] = retries
    _stuck_state["followpath_stuck_count"] = stuck_count
    _stuck_state["followpath_active"] = active
    _stuck_state["last_update"] = time.time()


def is_followpath_handling_stuck():
    if time.time() - _stuck_state["last_update"] > 10:
        return False
    return _stuck_state["followpath_active"] and _stuck_state["followpath_retries"] > 0
