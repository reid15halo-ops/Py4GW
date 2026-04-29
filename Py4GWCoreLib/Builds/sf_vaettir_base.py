import Py4GW
import math
from typing import Tuple
from Py4GWCoreLib import Routines
from Py4GWCoreLib import ConsoleLog
from Py4GWCoreLib import BuildMgr
from Py4GWCoreLib import Agent
from Py4GWCoreLib import Range
from Py4GWCoreLib import Player


class SFVaettirBase(BuildMgr):
    """Shared base for Shadow Form Vaettir farm builds (Assassin + Mesmer).
    Subclasses override __init__ (skill IDs) and ProcessSkillCasting (rotation)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Common skill IDs -- set by subclass __init__ after super().__init__()
        self.shadow_form = 0
        self.deadly_paradox = 0
        self.shroud_of_distress = 0
        self.heart_of_shadow = 0
        self.way_of_perfection = 0

        self.in_killing_routine = False
        self.routine_finished = False
        self.stuck_signal = False
        self.waypoint = (0, 0)

    # -- Shared state accessors ------------------------------------------------

    def SetKillingRoutine(self, in_killing_routine: bool):
        self.in_killing_routine = in_killing_routine

    def SetRoutineFinished(self, routine_finished: bool):
        self.routine_finished = routine_finished

    def GetStuckSignal(self) -> bool:
        return self.stuck_signal

    # -- Shared skill helpers --------------------------------------------------

    def _CastSkillID(self, skill_id: int, extra_condition: bool = True, log: bool = True, aftercast_delay: int = 1000):
        result = yield from Routines.Yield.Skills.CastSkillID(skill_id, extra_condition=extra_condition, log=log, aftercast_delay=aftercast_delay)
        return result

    def _CastSkillSlot(self, slot: int, extra_condition: bool = True, log: bool = True, aftercast_delay: int = 1000):
        result = yield from Routines.Yield.Skills.CastSkillSlot(slot, extra_condition=extra_condition, log=log, aftercast_delay=aftercast_delay)
        return result

    # -- Shared combat utilities -----------------------------------------------

    def vector_angle(self, a: Tuple[float, float], b: Tuple[float, float]) -> float:
        """Returns the cosine similarity (dot product / magnitudes). 1 = same direction, -1 = opposite."""
        mag_a = math.hypot(*a)
        mag_b = math.hypot(*b)
        if mag_a == 0 or mag_b == 0:
            return 1  # safest fallback
        dot = a[0] * b[0] + a[1] * b[1]
        return dot / (mag_a * mag_b)

    def CastShroudOfDistress(self):
        player_agent_id = Player.GetAgentID()
        if Agent.GetHealth(player_agent_id) < 0.45:
            ConsoleLog(self.build_name, "Casting Shroud of Distress.", Py4GW.Console.MessageType.Info, log=False)
            # ** Cast Shroud of Distress **
            yield from self._CastSkillID(self.shroud_of_distress, log=False, aftercast_delay=1750)
