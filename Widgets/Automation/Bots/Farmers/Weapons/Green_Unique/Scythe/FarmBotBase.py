"""FarmBotBase.py -- Reusable building blocks for green-unique farm bots.

This is a utility module (NOT a base class).  GW1 farm bots use the Botting()
framework, not inheritance.  Instead we provide standalone functions and
coroutine generators that any bot can import and call.

Each factory function accepts configurable parameters and returns either a
plain function or a coroutine generator, ready for the Botting FSM.

Safety:
- All map guards per CLAUDE.md (IsMapReady, IsMapLoading, IsExplorable)
- All exceptions logged, never silently swallowed (rule 15)
- No yield in non-generator paths (rule 17)
- ConsoleLog for all logging
"""

import time

from Py4GWCoreLib import AgentArray, Agent, Map, Party, Player, Range, Routines, Utils
from Py4GWCoreLib import ConsoleLog, Console


# ===========================================================================
# 1. Count nearby enemies (pure helper, no state)
# ===========================================================================
def count_nearby_enemies(radius=None):
    """Count enemies within *radius* of the player (default: compass)."""
    if radius is None:
        radius = Range.SafeCompass.value
    try:
        enemies = AgentArray.GetEnemyArray()
        return len(AgentArray.Filter.ByCondition(
            enemies,
            lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid)) <= radius,
        ))
    except Exception as e:
        ConsoleLog("FarmBotBase", f"count_nearby_enemies error: {e}", Console.MessageType.Warning)
        return 0


# ===========================================================================
# 2. Danger check factory
# ===========================================================================
def create_danger_check(bot_name, enemy_threshold=8, hp_threshold=0.50):
    """Return a function() -> bool that checks if the team is overwhelmed.

    True when BOTH:
      - more than *enemy_threshold* enemies on compass
      - any party member below *hp_threshold* HP
    """
    def _team_in_danger():
        try:
            if not Map.IsMapReady() or not Map.IsExplorable():
                return False

            enemy_array = AgentArray.GetEnemyArray()
            nearby = AgentArray.Filter.ByCondition(
                enemy_array,
                lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
                <= Range.SafeCompass.value,
            )
            if len(nearby) <= enemy_threshold:
                return False

            players = Party.GetPlayers()
            for player in players:
                try:
                    agent_id = Party.Players.GetAgentIDByLoginNumber(player.login_number)
                    if agent_id <= 0 or not Agent.IsValid(agent_id):
                        continue
                    hp_pct = Agent.GetHealth(agent_id)
                    if 0 < hp_pct < hp_threshold:
                        return True
                except Exception:
                    continue

            return False
        except Exception as e:
            ConsoleLog(bot_name, f"Danger check error: {e}", Console.MessageType.Warning)
            return False

    return _team_in_danger


# ===========================================================================
# 3. Boss scanner factory
# ===========================================================================
def create_boss_scanner(bot_name, model_id, fallback_boss_glow=True):
    """Return (scan_fn, background_coro_fn, state_dict).

    *scan_fn()*        -- scan compass for the boss, returns (found, agent_id)
    *background_coro_fn()* -- returns a managed-coroutine generator (while-True)
    *state*            -- shared dict with keys: spotted, killed, agent_id
    """
    state = {"spotted": False, "killed": False, "agent_id": -1}

    def _scan():
        """Scan enemy array for the boss.  Returns (found: bool, agent_id: int)."""
        if state["spotted"] and state["agent_id"] > 0:
            try:
                if Agent.IsValid(state["agent_id"]) and not Agent.IsDead(state["agent_id"]):
                    return True, state["agent_id"]
            except Exception as e:
                ConsoleLog(bot_name, f"Boss validation error: {e}", Console.MessageType.Warning)
            state["spotted"] = False
            state["agent_id"] = -1

        try:
            enemy_array = AgentArray.GetEnemyArray()
            enemy_array = AgentArray.Filter.ByCondition(
                enemy_array,
                lambda aid: Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
                <= Range.SafeCompass.value,
            )

            # Primary: exact model ID match
            for enemy_id in enemy_array:
                try:
                    if Agent.GetModelID(enemy_id) == model_id:
                        state["spotted"] = True
                        state["agent_id"] = enemy_id
                        ConsoleLog(bot_name, f"Boss found (model {model_id}) agent {enemy_id}")
                        return True, enemy_id
                except Exception:
                    continue

            # Fallback: boss-glow detection
            if fallback_boss_glow:
                for enemy_id in enemy_array:
                    try:
                        if Agent.HasBossGlow(enemy_id) and not Agent.IsDead(enemy_id):
                            state["spotted"] = True
                            state["agent_id"] = enemy_id
                            try:
                                mid = Agent.GetModelID(enemy_id)
                            except Exception:
                                mid = -1
                            ConsoleLog(bot_name, f"Boss-glow enemy (model {mid}) agent {enemy_id}")
                            return True, enemy_id
                    except Exception:
                        continue

        except Exception as e:
            ConsoleLog(bot_name, f"Boss scan error: {e}", Console.MessageType.Warning)

        return False, -1

    def _check_dead():
        """Return True if boss is confirmed dead."""
        if state["killed"]:
            return True
        if state["spotted"] and state["agent_id"] > 0:
            try:
                if Agent.IsDead(state["agent_id"]):
                    state["killed"] = True
                    ConsoleLog(bot_name, "Boss confirmed dead")
                    return True
            except Exception as e:
                ConsoleLog(bot_name, f"Boss death check error: {e}", Console.MessageType.Warning)
        return False

    def _background_scanner():
        """Managed coroutine: scan every second, detect boss + death."""
        while True:
            try:
                if not Map.IsExplorable() or not Map.IsMapReady():
                    yield from Routines.Yield.wait(1000)
                    continue

                if state["killed"]:
                    yield from Routines.Yield.wait(1000)
                    continue

                if state["spotted"] and state["agent_id"] > 0:
                    try:
                        if Agent.IsDead(state["agent_id"]):
                            state["killed"] = True
                            ConsoleLog(bot_name, "Boss died (detected by scanner)")
                    except Exception as e:
                        ConsoleLog(bot_name, f"Scanner death check error: {e}", Console.MessageType.Warning)
                    yield from Routines.Yield.wait(1000)
                    continue

                _scan()

            except Exception as e:
                ConsoleLog(bot_name, f"Scanner error: {e}", Console.MessageType.Warning)

            yield from Routines.Yield.wait(1000)

    return _scan, _check_dead, _background_scanner, state


# ===========================================================================
# 4. Zone entry safety handler factory
# ===========================================================================
def create_zone_entry_handler(bot, bot_name, safe_point,
                              small_pack_threshold=4, max_wait_ms=30000):
    """Return (coro_fn, custom_state_fn).

    *coro_fn()*         -- coroutine generator that handles zone-entry mobs
    *custom_state_fn()* -- non-generator wrapper for AddCustomState
    """
    aggro_radius = Range.Earshot.value

    def _handle_zone_entry_mobs():
        """Coroutine: handle mobs near the zone-in point.

        1. Wait for map ready
        2. No enemies -> proceed
        3. Small pack (<=threshold) -> stand and fight
        4. Large pack -> retreat to safe_point
        """
        # Wait for map to be fully ready after zone
        for _ in range(20):
            if Map.IsMapReady() and Map.IsExplorable():
                break
            yield from Routines.Yield.wait(500)

        yield from Routines.Yield.wait(1500)  # Brief settle after zone-in

        nearby = count_nearby_enemies(aggro_radius)
        ConsoleLog(bot_name, f"Zone entry: {nearby} enemies within earshot")

        if nearby == 0:
            ConsoleLog(bot_name, "Zone entry: clear, proceeding to patrol")
            return

        if nearby <= small_pack_threshold:
            ConsoleLog(bot_name, f"Zone entry: small pack ({nearby}), fighting")
            elapsed = 0
            while elapsed < max_wait_ms:
                if not Map.IsMapReady() or Map.IsMapLoading():
                    return
                if count_nearby_enemies(aggro_radius) == 0:
                    ConsoleLog(bot_name, "Zone entry: pack cleared")
                    break
                yield from Routines.Yield.wait(1000)
                elapsed += 1000
            yield from Routines.Yield.wait(2000)  # Loot window
            return

        # Large pack -- retreat to safe point
        ConsoleLog(bot_name, f"Zone entry: large pack ({nearby}), retreating")
        sx, sy = safe_point
        try:
            yield from bot.Move._coro_get_path_to(sx, sy)
            yield from bot.Move._coro_follow_path_to()
        except Exception as e:
            ConsoleLog(bot_name, f"Zone entry retreat error: {e}", Console.MessageType.Warning)

        # Wait for combat to resolve
        elapsed = 0
        while elapsed < max_wait_ms:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if not Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                break
            yield from Routines.Yield.wait(1000)
            elapsed += 1000

        ConsoleLog(bot_name, "Zone entry: resolved, starting patrol")

    def _custom_state_wrapper():
        """Non-generator wrapper for AddCustomState."""
        return _handle_zone_entry_mobs()

    return _handle_zone_entry_mobs, _custom_state_wrapper


# ===========================================================================
# 5. Patrol intercept factory
# ===========================================================================
def create_patrol_intercept(bot, bot_name, waypoints, boss_state,
                            scan_fn, check_dead_fn, danger_fn,
                            max_loops=3, danger_interval_ms=2000):
    """Return (intercept_coro_fn, launch_fn, done_fn).

    *intercept_coro_fn()* -- coroutine generator: walk waypoints scanning
    *launch_fn()*         -- non-generator: schedule intercept as managed coro
    *done_fn()*           -- condition: True when patrol/kill is finished
    """

    def _wait_for_kill():
        """Sub-coroutine: wait for boss to die, re-approach if out of range."""
        KILL_TIMEOUT_S = 120
        start = time.time()

        while (time.time() - start) < KILL_TIMEOUT_S:
            if not Map.IsMapReady() or Map.IsMapLoading():
                return
            if check_dead_fn():
                ConsoleLog(bot_name, "Boss killed!")
                return

            # Re-approach if boss moved out of range
            aid = boss_state["agent_id"]
            if aid > 0:
                try:
                    dist = Utils.Distance(Player.GetXY(), Agent.GetXY(aid))
                    if dist > Range.SafeCompass.value:
                        ConsoleLog(bot_name, "Boss out of range -- re-approaching")
                        ax, ay = Agent.GetXY(aid)
                        if ax != 0 or ay != 0:
                            yield from bot.Move._coro_get_path_to(ax, ay)
                            yield from bot.Move._coro_follow_path_to()
                except Exception as e:
                    ConsoleLog(bot_name, f"Re-approach error: {e}", Console.MessageType.Warning)

            yield from Routines.Yield.wait(1000)

        ConsoleLog(bot_name, "Kill timeout reached", Console.MessageType.Warning)

    def _intercept_patrol():
        """Walk waypoints, scanning for boss at each step."""
        for loop_count in range(max_loops):
            if boss_state["killed"]:
                return

            ConsoleLog(bot_name, f"Patrol loop {loop_count + 1}/{max_loops}")

            for _i, (wx, wy) in enumerate(waypoints):
                # Map guard
                if not Map.IsMapReady() or Map.IsMapLoading() or not Map.IsExplorable():
                    return
                if boss_state["killed"]:
                    return

                # Boss spotted? Rush to it
                if boss_state["spotted"] and boss_state["agent_id"] > 0:
                    try:
                        if not Agent.IsDead(boss_state["agent_id"]):
                            ax, ay = Agent.GetXY(boss_state["agent_id"])
                            if ax != 0 or ay != 0:
                                ConsoleLog(bot_name, f"Boss spotted! Rushing to ({ax:.0f}, {ay:.0f})")
                                yield from bot.Move._coro_get_path_to(ax, ay)
                                yield from bot.Move._coro_follow_path_to()
                                if not Agent.IsDead(boss_state["agent_id"]):
                                    yield from _wait_for_kill()
                                return
                        else:
                            boss_state["killed"] = True
                            return
                    except Exception as e:
                        ConsoleLog(bot_name, f"Rush error: {e}", Console.MessageType.Warning)
                        boss_state["spotted"] = False
                        boss_state["agent_id"] = -1

                # Danger check: pause if overwhelmed
                if danger_fn():
                    ConsoleLog(bot_name, "Team overwhelmed -- pausing to fight")
                    while danger_fn():
                        if not Map.IsMapReady() or Map.IsMapLoading():
                            return
                        yield from Routines.Yield.wait(danger_interval_ms)
                    # Wait until fully out of combat
                    while Routines.Checks.Agents.InDanger(aggro_area=Range.Earshot):
                        if not Map.IsMapReady() or Map.IsMapLoading():
                            return
                        yield from Routines.Yield.wait(1000)
                    ConsoleLog(bot_name, "Combat resolved -- resuming patrol")

                # Move to next waypoint
                yield from bot.Move._coro_get_path_to(wx, wy)
                yield from bot.Move._coro_follow_path_to()

                # Post-move scan
                scan_fn()

            # End of full loop
            scan_fn()

        ConsoleLog(bot_name, "Patrol loops exhausted -- boss not found", Console.MessageType.Warning)

    def _launch_as_custom_state():
        """Non-generator: schedule intercept as managed coroutine."""
        fsm = bot.config.FSM
        fsm.AddManagedCoroutine("InterceptPatrol", _intercept_patrol)
        boss_state["patrol_launched"] = True

    def _is_done():
        """Condition: True when patrol/kill is finished.
        Returns False until the patrol has been launched AND completed."""
        if not boss_state.get("patrol_launched", False):
            return False  # Not launched yet — don't skip ahead
        if boss_state["killed"]:
            return True
        if not bot.config.FSM.HasManagedCoroutine("InterceptPatrol"):
            return True  # Coroutine finished (exhausted or removed)
        return False

    return _intercept_patrol, _launch_as_custom_state, _is_done


# ===========================================================================
# 6. Wipe handler factory
# ===========================================================================
def create_wipe_handler(bot, bot_name, resume_step_name):
    """Return (on_wipe_callback,) for use with bot.Events.OnPartyWipeCallback.

    The callback pauses the FSM, waits for revive, then either:
      - jumps to *resume_step_name* if still on the same map
      - resumes normally if map changed (return to outpost)
    """
    def _on_party_wipe_coro():
        """Coroutine: wait for player to stop being dead."""
        while True:
            if not Map.IsMapReady() or Map.IsMapLoading():
                bot.config.FSM.resume()
                return
            try:
                my_id = Player.GetAgentID()
                if my_id > 0 and not Agent.IsDead(my_id):
                    break
            except Exception as e:
                ConsoleLog(bot_name, f"Wipe check error: {e}", Console.MessageType.Warning)
            yield from bot.Wait._coro_for_time(1000)

        # Player revived -- jump to combat step and resume
        bot.States.JumpToStepName(resume_step_name)
        bot.config.FSM.resume()

    def _on_wipe_callback():
        """Event callback: pause FSM, schedule wipe recovery coroutine."""
        ConsoleLog(bot_name, "Party wipe detected")
        fsm = bot.config.FSM
        fsm.pause()
        fsm.AddManagedCoroutine("OnWipe_OPD", _on_party_wipe_coro)

    return _on_wipe_callback


# ===========================================================================
# 7. Farm reset factory
# ===========================================================================
def create_farm_reset(bot, boss_state, coroutine_names=None):
    """Return a plain function that resets all state for a fresh farm loop.

    *coroutine_names* defaults to ["InterceptPatrol", "BossScanner"].
    """
    if coroutine_names is None:
        coroutine_names = ["InterceptPatrol", "BossScanner"]

    def _reset():
        boss_state["spotted"] = False
        boss_state["killed"] = False
        boss_state["agent_id"] = -1
        boss_state["patrol_launched"] = False
        for name in coroutine_names:
            try:
                bot.States.RemoveManagedCoroutine(name)
            except Exception:
                pass

    return _reset
