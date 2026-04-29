"""Kit Restock Utility — ensures Custom Behaviors chars always have enough kits.

Runs once per outpost visit (reset on map change). Checks inventory for:
  - 3x Salvage Kit (model 2992)
  - 1x Superior Identification Kit (model 5899)
  - 1x Superior Salvage Kit (model 5900)

If anything is missing, walks to the nearest merchant, opens the window,
and buys the missing kits.
"""

from typing import Any, Generator, override

from Py4GWCoreLib import (
    GLOBAL_CACHE, AgentArray, Routines, Range, Map, Agent, Player,
    ItemArray,
)
from Py4GWCoreLib.Py4GWcorelib import Utils
from Py4GWCoreLib.Pathing import AutoPathing
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.bus.event_bus import EventBus
from Sources.oazix.CustomBehaviors.primitives.bus.event_message import EventMessage
from Sources.oazix.CustomBehaviors.primitives.bus.event_type import EventType
from Sources.oazix.CustomBehaviors.primitives.helpers import custom_behavior_helpers
from Sources.oazix.CustomBehaviors.primitives.helpers.behavior_result import BehaviorResult
from Sources.oazix.CustomBehaviors.primitives.scores.comon_score import CommonScore
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase
from Sources.oazix.CustomBehaviors.primitives.skills.utility_skill_execution_strategy import UtilitySkillExecutionStrategy
from Sources.oazix.CustomBehaviors.primitives.skills.utility_skill_typology import UtilitySkillTypology
from Sources.oazix.CustomBehaviors.primitives import constants


# ─── Kit targets ─────────────────────────────────────────────────────────────
_SALVAGE_KIT_MODEL = 2992
_SUPERIOR_ID_KIT_MODEL = 5899
_SUPERIOR_SALVAGE_KIT_MODEL = 5900

_KIT_TARGETS = {
    _SALVAGE_KIT_MODEL: 3,
    _SUPERIOR_ID_KIT_MODEL: 1,
    _SUPERIOR_SALVAGE_KIT_MODEL: 1,
}


class KitRestockUtility(CustomSkillUtilityBase):
    def __init__(
        self,
        event_bus: EventBus,
        current_build: list[CustomSkill],
    ) -> None:
        super().__init__(
            event_bus=event_bus,
            skill=CustomSkill("kit_restock_utility"),
            in_game_build=current_build,
            score_definition=ScoreStaticDefinition(CommonScore.INVENTORY.value),
            allowed_states=[BehaviorState.IDLE],
            utility_skill_typology=UtilitySkillTypology.INVENTORY,
            execution_strategy=UtilitySkillExecutionStrategy.EXECUTE_THROUGH_THE_END,
        )

        self._outpost_checked = False
        event_bus.subscribe(
            EventType.MAP_CHANGED,
            self._on_map_changed,
            subscriber_name=self.custom_skill.skill_name,
        )

    def _on_map_changed(self, message: EventMessage) -> Generator[Any, Any, Any]:
        self._outpost_checked = False
        yield

    @override
    def are_common_pre_checks_valid(self, current_state: BehaviorState) -> bool:
        if self.allowed_states is not None and current_state not in self.allowed_states:
            return False
        if not Map.IsOutpost():
            return False
        return True

    def _get_missing_kits(self) -> dict[int, int]:
        """Return {model_id: amount_to_buy} for every kit we're short on."""
        missing = {}
        for model_id, target_count in _KIT_TARGETS.items():
            have = GLOBAL_CACHE.Inventory.GetModelCount(model_id)
            need = target_count - have
            if need > 0:
                missing[model_id] = need
        return missing

    def _is_merchant_agent(self, agent_id: int) -> bool:
        merchant_tags = ["Merchant", "Marchand", "Kauffrau"]
        agent_name = Agent.GetNameByID(agent_id)
        return any(tag in agent_name for tag in merchant_tags)

    def _get_merchant(self) -> int | None:
        agents = AgentArray.GetNPCMinipetArray()
        agents = AgentArray.Filter.ByDistance(agents, Player.GetXY(), Range.Compass.value)
        agents = AgentArray.Filter.ByCondition(
            agents, lambda aid: Agent.IsAlive(aid) and Agent.IsValid(aid)
        )
        agents = AgentArray.Filter.ByCondition(agents, self._is_merchant_agent)
        return agents[0] if agents else None

    def _buy_kits(self, missing: dict[int, int]) -> Generator[Any, None, None]:
        """Buy missing kits from an open merchant window."""
        from Py4GWCoreLib.py4gwcorelib_src.ActionQueue import ActionQueueManager

        for model_id, amount in missing.items():
            offered = GLOBAL_CACHE.Trading.Merchant.GetOfferedItems()
            if not offered:
                if constants.DEBUG:
                    print(f"[KitRestock] Merchant window closed, aborting.")
                return

            matches = ItemArray.Filter.ByCondition(
                offered, lambda item_id: GLOBAL_CACHE.Item.GetModelID(item_id) == model_id
            )
            if not matches:
                if constants.DEBUG:
                    print(f"[KitRestock] Model {model_id} not offered by this merchant.")
                continue

            item_id = matches[0]
            value = GLOBAL_CACHE.Item.Properties.GetValue(item_id) * 2
            for _ in range(amount):
                GLOBAL_CACHE.Trading.Merchant.BuyItem(item_id, value)

            # Wait for buy queue to finish
            timeout = 0
            while not ActionQueueManager().IsEmpty("MERCHANT") and timeout < 50:
                yield from Routines.Yield.wait(50)
                timeout += 1

            if constants.DEBUG:
                print(f"[KitRestock] Bought {amount}x model {model_id}.")

    @override
    def _evaluate(self, current_state: BehaviorState, previously_attempted_skills: list[CustomSkill]) -> float | None:
        if self._outpost_checked:
            return None
        missing = self._get_missing_kits()
        if missing:
            return self.score_definition.get_score()
        self._outpost_checked = True
        return None

    @override
    def _execute(self, state: BehaviorState) -> Generator[Any, None, BehaviorResult]:
        missing = self._get_missing_kits()
        if not missing:
            self._outpost_checked = True
            return BehaviorResult.ACTION_SKIPPED

        merchant_id = self._get_merchant()
        if merchant_id is None:
            if constants.DEBUG:
                print("[KitRestock] No merchant found.")
            self._outpost_checked = True
            return BehaviorResult.ACTION_SKIPPED

        # Walk to merchant if far away
        target_pos = Agent.GetXY(merchant_id)
        if Utils.Distance(target_pos, Player.GetXY()) > 150:
            path3d = yield from AutoPathing().get_path_to(
                target_pos[0], target_pos[1],
                smooth_by_los=True, margin=100.0, step_dist=300.0,
            )
            path2d = [(x, y) for (x, y, *_) in path3d]
            yield from Routines.Yield.Movement.FollowPath(
                path_points=path2d,
                custom_exit_condition=lambda: Agent.IsDead(Player.GetAgentID()),
                tolerance=150,
                log=constants.DEBUG,
                timeout=10_000,
            )

        # Open merchant window
        Player.Interact(merchant_id, call_target=True)
        yield from custom_behavior_helpers.Helpers.wait_for(2_000)  # Wait for window

        # Re-check in case kits were bought by another utility
        missing = self._get_missing_kits()
        if missing:
            yield from self._buy_kits(missing)
            yield from custom_behavior_helpers.Helpers.wait_for(1_000)

        self._outpost_checked = True
        return BehaviorResult.ACTION_PERFORMED

    @override
    def customized_debug_ui(self, current_state: BehaviorState) -> None:
        import PyImGui
        if PyImGui.collapsing_header("Kit Restock", PyImGui.TreeNodeFlags.DefaultOpen):
            PyImGui.text(f"Checked this outpost: {self._outpost_checked}")
            missing = self._get_missing_kits()
            if missing:
                PyImGui.text_colored("Missing kits:", (1.0, 0.5, 0.0, 1.0))
                for mid, amt in missing.items():
                    PyImGui.bullet_text(f"Model {mid}: need {amt}")
            else:
                PyImGui.text_colored("All kits stocked.", (0.0, 1.0, 0.0, 1.0))
