"""
ActionSafetyLayer — Pre-flight safety checks for all queued game actions.

Patches ActionQueueNode.execute_next() to add:
1. Map readiness check (prevents actions during loading)
2. Dialog blocking check (prevents actions while dialog open)
3. Exception handling (prevents crash propagation)
4. Throttled error logging (prevents console overflow)

Install once at startup: ActionSafetyLayer.install()
"""

import time

_safety_error_count = [0]
_original_execute_next = None


def _is_any_blocking_dialog_open():
    """Unified dialog check — delegates to UIManager.IsAnyBlockingDialogOpen()."""
    try:
        from Py4GWCoreLib import UIManager
        return UIManager.IsAnyBlockingDialogOpen()
    except Exception:
        return True  # Fail-closed: assume dialog open if we can't check


def _safe_execute_next(self):
    """Patched execute_next() with pre-flight safety checks."""
    global _original_execute_next, _safety_error_count

    # O(1) queue name lookup — cached at install time
    queue_name = getattr(self, '_safety_queue_name', '')

    # 1. Map loading check — TRANSITION queue is exempt (needs to fire during zone)
    if queue_name != "TRANSITION":
        try:
            from Py4GWCoreLib import Map
            if Map.IsMapLoading() or not Map.IsMapReady():
                self.action_queue_timer.Reset()  # Prevent burst-fire on unblock
                return False
        except Exception:
            self.action_queue_timer.Reset()
            return False  # Fail-closed: block if we can't verify map state

    # 2. Dialog blocking check — FAST queue is exempt (UI ops may need dialogs)
    if queue_name != "FAST":
        if _is_any_blocking_dialog_open():
            self.action_queue_timer.Reset()  # Prevent burst-fire on unblock
            return False

    # 3. Execute original with exception handling
    try:
        return _original_execute_next(self)
    except Exception as e:
        _safety_error_count[0] += 1
        if _safety_error_count[0] > 100000:
            _safety_error_count[0] = 1  # Wrap to prevent unbounded growth
        if _safety_error_count[0] % 100 == 1:
            try:
                import Py4GW
                Py4GW.Console.Log(
                    "ActionSafetyLayer",
                    f"Action error #{_safety_error_count[0]}: {e}",
                    Py4GW.Console.MessageType.Warning,
                )
            except Exception:
                pass
        return False


class ActionSafetyLayer:
    """Install / uninstall the safety wrapper around ActionQueueNode.execute_next()."""

    @staticmethod
    def install():
        global _original_execute_next
        if _original_execute_next is not None:
            return  # Already patched — idempotent

        from .ActionQueue import ActionQueueNode, ActionQueueManager

        _original_execute_next = ActionQueueNode.execute_next
        ActionQueueNode.execute_next = _safe_execute_next

        # Cache queue name on each node for O(1) lookup at runtime
        mgr = ActionQueueManager()
        for name, node in mgr.queues.items():
            node._safety_queue_name = name

    @staticmethod
    def uninstall():
        global _original_execute_next, _safety_error_count
        if _original_execute_next is None:
            return

        from .ActionQueue import ActionQueueNode, ActionQueueManager

        ActionQueueNode.execute_next = _original_execute_next
        _original_execute_next = None
        _safety_error_count[0] = 0

        # Clean up cached queue names
        try:
            mgr = ActionQueueManager()
            for node in mgr.queues.values():
                if hasattr(node, '_safety_queue_name'):
                    delattr(node, '_safety_queue_name')
        except Exception:
            pass
