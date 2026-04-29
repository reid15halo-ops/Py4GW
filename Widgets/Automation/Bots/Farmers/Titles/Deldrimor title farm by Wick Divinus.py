# region Imports & Config
from Py4GWCoreLib import Botting, Routines, GLOBAL_CACHE, ModelID, Agent, Player, ConsoleLog, IniManager
from Py4GWCoreLib.enums_src.Title_enums import TitleID, TITLE_TIERS
from Py4GWCoreLib.botting_src.property import Property
import Py4GW
import os
import time

MODULE_NAME = "Deldrimor Title Farm"
MODULE_ICON = "Textures/Skill_Icons/[2424] - Stout-Hearted.jpg"

bot = Botting("Deldrimor title farm by Wick Divinus",
              upkeep_honeycomb_active=True)

bot.config.config_properties.use_conset = Property(bot.config, "use_conset", active=False)
bot.config.config_properties.use_pcons = Property(bot.config, "use_pcons", active=False)
bot.config.config_properties.use_custom_behaviors = Property(bot.config, "use_custom_behaviors", active=True)

_SETTINGS_SECTION = "TitleBotSettings"
_BEHAVIOR_MODE_KEY = "use_custom_behaviors"

# (model_id, effect_skill_name) — single source of truth for consumable use & restock
CONSET_ITEMS: list[tuple[int, str]] = [
    (ModelID.Essence_Of_Celerity.value, "Essence_of_Celerity_item_effect"),
    (ModelID.Grail_Of_Might.value,      "Grail_of_Might_item_effect"),
    (ModelID.Armor_Of_Salvation.value,  "Armor_of_Salvation_item_effect"),
]

PCON_ITEMS: list[tuple[int, str]] = [
    (ModelID.Birthday_Cupcake.value,      "Birthday_Cupcake_skill"),
    (ModelID.Golden_Egg.value,            "Golden_Egg_skill"),
    (ModelID.Candy_Corn.value,            "Candy_Corn_skill"),
    (ModelID.Candy_Apple.value,           "Candy_Apple_skill"),
    (ModelID.Slice_Of_Pumpkin_Pie.value,  "Pie_Induced_Ecstasy"),
    (ModelID.Drake_Kabob.value,           "Drake_Skin"),
    (ModelID.Bowl_Of_Skalefin_Soup.value, "Skale_Vigor"),
    (ModelID.Pahnai_Salad.value,          "Pahnai_Salad_item_effect"),
    (ModelID.War_Supplies.value,          "Well_Supplied"),
]

CONSET_RESTOCK_MODELS = [m for m, _ in CONSET_ITEMS]
PCON_RESTOCK_MODELS   = [m for m, _ in PCON_ITEMS] + [
    ModelID.Honeycomb.value,
    ModelID.Scroll_Of_Resurrection.value,
]
# endregion


# region Bot Routine
def Routine(bot: Botting) -> None:
    PrepareForCombat(bot)
    Snowman(bot)


# Rough bridge-entrance coords in the Snowman map where char geometry causes
# the player to snag. Detection radius is generous (400u) since these are
# approximate.
BRIDGE_ENTRANCE_COORDS = [
    (-18112, 3625),
    (-12767, -2918),
    (-14555, 2745),
    (-14555, 3590),
    (-12916, 4633),
    (-12146, 5552),
    (-11426, 5502),
    (-7923, 5701),
    (-5694, 6133),
    (-5247, 6307),
    (-3558, 5661),
    (-2217, 6506),
    (-5463, 12515),
    (-11916, -1505),
    (-8520, -1893),
    (-6725, -3494),
    # Confirmed stuck-twice cluster (defensive — even with detour active)
    (-14448, 3413),
    (-14441, 3499),
    (-14441, 3578),
    (-14439, 3652),
]


def _do_bridge_unstuck():
    """Unstuck recipe: /stuck, walk back, strafe both ways, /stuck again."""
    Player.SendChatCommand("stuck")
    yield from Routines.Yield.wait(500)
    yield from Routines.Yield.Movement.WalkBackwards(1000)
    yield from Routines.Yield.Movement.StrafeLeft(1000)
    yield from Routines.Yield.wait(200)
    yield from Routines.Yield.Movement.StrafeRight(1500)
    Player.SendChatCommand("stuck")
    yield from Routines.Yield.wait(500)


def StuckBridgeWatchdog(bot: Botting):
    """Detect player stuck near known-problem bridge entrances and unstuck.

    Polls every 2s. Position is sampled and compared. If player is within
    DETECT_RADIUS of any BRIDGE_ENTRANCE_COORDS spot AND position hasn't
    changed more than MIN_MOVE_UNITS for STUCK_TICKS consecutive samples,
    runs the unstuck recipe and applies a 30s cooldown.
    """
    DETECT_RADIUS = 400
    DETECT_RADIUS_SQ = DETECT_RADIUS * DETECT_RADIUS
    POLL_MS = 2000
    MIN_MOVE_UNITS = 50
    MIN_MOVE_SQ = MIN_MOVE_UNITS * MIN_MOVE_UNITS
    STUCK_TICKS = 5  # 5 × 2s = 10s of no movement
    COOLDOWN_S = 30

    from Py4GWCoreLib import Map
    last_x, last_y = 0.0, 0.0
    stuck_count = 0
    cooldown_until = 0.0
    while True:
        try:
            now = time.time()
            if now < cooldown_until:
                yield from Routines.Yield.wait(POLL_MS)
                continue
            if not Map.IsMapReady() or Map.IsMapLoading():
                stuck_count = 0
                yield from Routines.Yield.wait(POLL_MS)
                continue
            try:
                px, py = Player.GetXY()
            except Exception:
                yield from Routines.Yield.wait(POLL_MS)
                continue

            # Near any bridge entrance?
            near_bridge = False
            for bx, by in BRIDGE_ENTRANCE_COORDS:
                dx = px - bx
                dy = py - by
                if dx * dx + dy * dy <= DETECT_RADIUS_SQ:
                    near_bridge = True
                    break
            if not near_bridge:
                stuck_count = 0
                last_x, last_y = px, py
                yield from Routines.Yield.wait(POLL_MS)
                continue

            # Movement check
            mdx = px - last_x
            mdy = py - last_y
            if (mdx * mdx + mdy * mdy) < MIN_MOVE_SQ:
                stuck_count += 1
            else:
                stuck_count = 0
            last_x, last_y = px, py

            if stuck_count >= STUCK_TICKS:
                ConsoleLog("BridgeUnstuck",
                           f"stuck near bridge at ({int(px)},{int(py)}) — unstucking",
                           Py4GW.Console.MessageType.Warning)
                yield from _do_bridge_unstuck()
                stuck_count = 0
                cooldown_until = time.time() + COOLDOWN_S
        except Exception as e:
            ConsoleLog("BridgeUnstuck", f"watchdog error: {e}",
                       Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(POLL_MS)


def MoraleBoostWatchdog(bot: Botting):
    """Monitor master morale and burn honeycombs until at +10% boost (morale=110).

    Per CLAUDE.md rule 26 — master is identified by email, not party-leader.
    Polls every 30s. When morale <110 AND in explorable, uses honeycombs in a
    tight loop (8s between uses to let server-side cooldown clear) until
    threshold reached or out of honeycombs.

    Death penalty in GW1 = morale 0..99. +10% boost = morale 110.
    """
    POLL_MS = 30000
    USE_TIMEOUT_S = 8
    MASTER_EMAIL = "jonasglawion@aol.com"
    MAX_PER_BURST = 15

    from Py4GWCoreLib import Map
    while True:
        try:
            try:
                local_email = Player.GetAccountEmail() or ""
            except Exception:
                local_email = ""
            if local_email != MASTER_EMAIL:
                yield from Routines.Yield.wait(POLL_MS)
                continue
            if not Map.IsMapReady() or Map.IsMapLoading() or Map.IsOutpost():
                yield from Routines.Yield.wait(POLL_MS)
                continue
            try:
                current_morale = int(Player.GetMorale())
            except Exception:
                yield from Routines.Yield.wait(POLL_MS)
                continue
            if current_morale >= 110:
                yield from Routines.Yield.wait(POLL_MS)
                continue

            ConsoleLog("MoraleBoost",
                       f"morale={current_morale} (DP detected: {current_morale < 100}) — burning honeycombs until +10%",
                       Py4GW.Console.MessageType.Info)
            attempts = 0
            while current_morale < 110 and attempts < MAX_PER_BURST:
                # Abort burst if map is no longer in a valid use-item state.
                # Without this guard, UseItem during a /resign loading screen
                # froze the master client mid-load.
                if not Map.IsMapReady() or Map.IsMapLoading() or Map.IsOutpost():
                    ConsoleLog("MoraleBoost",
                               f"map state changed mid-burst (loading/outpost) — aborting burst at attempt {attempts}",
                               Py4GW.Console.MessageType.Warning)
                    break
                attempts += 1
                honey_id = GLOBAL_CACHE.Inventory.GetFirstModelID(ModelID.Honeycomb.value)
                if not honey_id:
                    ConsoleLog("MoraleBoost",
                               f"out of honeycombs at morale={current_morale} after {attempts-1} uses",
                               Py4GW.Console.MessageType.Warning)
                    break
                GLOBAL_CACHE.Inventory.UseItem(honey_id)
                # Wait for the buff to apply, polling every 500ms
                wait_start = time.time()
                last_morale = current_morale
                while time.time() - wait_start < USE_TIMEOUT_S:
                    yield from Routines.Yield.wait(500)
                    # Also abort the per-honeycomb wait if map transitions
                    if Map.IsMapLoading() or Map.IsOutpost():
                        break
                    try:
                        current_morale = int(Player.GetMorale())
                    except Exception:
                        continue
                    if current_morale > last_morale:
                        break
                ConsoleLog("MoraleBoost",
                           f"after honeycomb #{attempts}: morale={current_morale}",
                           Py4GW.Console.MessageType.Info)
            ConsoleLog("MoraleBoost",
                       f"burst end: morale={current_morale}, used {attempts} honeycombs",
                       Py4GW.Console.MessageType.Info)
        except Exception as e:
            ConsoleLog("MoraleBoost", f"error: {e}", Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(POLL_MS)


def CombatLootToggle(bot: Botting):
    """Smart HeroAI Combat + Looting toggle for the Deldrimor farm.

    Combat/Targeting:
      - ON  when at least one valid enemy is within Spellcast range (1248u)
      - OFF when no enemy is in range
    Looting:
      - OFF by default
      - Opens for 15s every time combat ENDS (enemies were present last
        tick, none now)
      - Closes again after the window expires
      - Always OFF while enemies are in cast range
    """
    POLL_MS = 1000
    CAST_RANGE_SQ = 1248 * 1248
    LOOT_WINDOW_S = 15

    from Py4GWCoreLib import Map, AgentArray
    last_combat_state = None
    last_loot_state = None
    enemies_last_tick = False
    loot_until = 0.0

    while True:
        try:
            if not Map.IsMapReady() or Map.IsMapLoading() or Map.IsOutpost():
                yield from Routines.Yield.wait(POLL_MS)
                continue
            try:
                px, py = Player.GetXY()
            except Exception:
                yield from Routines.Yield.wait(POLL_MS)
                continue

            enemies_nearby = False
            try:
                for eid in AgentArray.GetEnemyArray():
                    try:
                        if not Agent.IsValid(eid) or Agent.IsDead(eid):
                            continue
                        ex, ey = Agent.GetXY(eid)
                    except Exception:
                        continue
                    dx = ex - px
                    dy = ey - py
                    if dx * dx + dy * dy <= CAST_RANGE_SQ:
                        enemies_nearby = True
                        break
            except Exception:
                pass

            now = time.time()
            # Edge: combat just ended → open 15s loot window
            if enemies_last_tick and not enemies_nearby:
                loot_until = now + LOOT_WINDOW_S
                ConsoleLog("CombatLoot",
                           f"combat ended — looting open for {LOOT_WINDOW_S}s",
                           Py4GW.Console.MessageType.Info)
            enemies_last_tick = enemies_nearby

            target_combat = enemies_nearby
            target_loot = (not enemies_nearby) and (now < loot_until)

            if target_combat != last_combat_state or target_loot != last_loot_state:
                try:
                    email = Player.GetAccountEmail() or ""
                    if not email:
                        # Fallback: ShMem lookup via local agent
                        try:
                            local_id = Player.GetAgentID()
                            for acc in GLOBAL_CACHE.ShMem.GetAllAccountData() or []:
                                if not acc:
                                    continue
                                if not getattr(acc, "IsAccount", False):
                                    continue
                                if acc.AgentData.AgentID == local_id:
                                    email = acc.AccountEmail or ""
                                    break
                        except Exception:
                            pass
                    if email:
                        opts = GLOBAL_CACHE.ShMem.GetHeroAIOptionsFromEmail(email)
                        if opts is not None:
                            if target_combat != last_combat_state:
                                opts.Combat = target_combat
                                opts.Targeting = target_combat
                                ConsoleLog("CombatLoot",
                                           f"Combat/Targeting -> {target_combat}",
                                           Py4GW.Console.MessageType.Info)
                                last_combat_state = target_combat
                            if target_loot != last_loot_state:
                                opts.Looting = target_loot
                                ConsoleLog("CombatLoot",
                                           f"Looting -> {target_loot}",
                                           Py4GW.Console.MessageType.Info)
                                last_loot_state = target_loot
                except Exception as e:
                    ConsoleLog("CombatLoot", f"toggle error: {e}",
                               Py4GW.Console.MessageType.Error)
        except Exception as e:
            ConsoleLog("CombatLoot", f"watchdog error: {e}",
                       Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(POLL_MS)


def BridgeApproachGuard(bot: Botting):
    """Proactively suspend custom/bot behaviour when player approaches a known
    bridge entrance. Bridges have geometry that snags the char mid-action when
    the combat/HeroAI logic is active. While in bridge zone, set
    'use_custom_behaviors' to False so the char traverses cleanly. Restores
    the prior state on exit.

    SAFETY: max MAX_ZONE_TIME_S in pause-state. If the watchdog ever crashes
    or the bot spends too long near a bridge, custom_behaviors gets force-
    restored to True. Without this, a stuck pause-state propagates via the
    CustomBehaviorParty broadcast and disables custom_behaviors fleet-wide.
    """
    DETECT_RADIUS = 400
    DETECT_RADIUS_SQ = DETECT_RADIUS * DETECT_RADIUS
    POLL_MS = 1500
    MAX_ZONE_TIME_S = 60

    from Py4GWCoreLib import Map
    in_bridge_zone = False
    saved_state = None
    zone_entered_at = 0.0
    while True:
        try:
            if not Map.IsMapReady() or Map.IsMapLoading():
                yield from Routines.Yield.wait(POLL_MS)
                continue
            try:
                px, py = Player.GetXY()
            except Exception:
                yield from Routines.Yield.wait(POLL_MS)
                continue

            near = False
            for bx, by in BRIDGE_ENTRANCE_COORDS:
                dx = px - bx
                dy = py - by
                if dx * dx + dy * dy <= DETECT_RADIUS_SQ:
                    near = True
                    break

            now = time.time()

            # Zone-time safety: if we've been "in zone" too long, force-restore.
            # Bridges shouldn't take >60s; if we're stuck, the leak hurts more
            # than the lack of pause.
            if in_bridge_zone and (now - zone_entered_at) > MAX_ZONE_TIME_S:
                ConsoleLog("BridgeGuard",
                           f"zone timeout ({int(now - zone_entered_at)}s > {MAX_ZONE_TIME_S}s) — force-restoring custom behaviours",
                           Py4GW.Console.MessageType.Warning)
                try:
                    bot.Properties.ApplyNow("use_custom_behaviors", "active", True)
                except Exception:
                    pass
                in_bridge_zone = False
                saved_state = None

            if near and not in_bridge_zone:
                try:
                    saved_state = _as_bool(bot.Properties.Get("use_custom_behaviors", "active"))
                except Exception:
                    saved_state = True
                if saved_state:
                    ConsoleLog("BridgeGuard",
                               f"entering bridge zone ({int(px)},{int(py)}) — pausing custom behaviours",
                               Py4GW.Console.MessageType.Info)
                    try:
                        bot.Properties.ApplyNow("use_custom_behaviors", "active", False)
                    except Exception as e:
                        ConsoleLog("BridgeGuard", f"disable error: {e}",
                                   Py4GW.Console.MessageType.Error)
                in_bridge_zone = True
                zone_entered_at = now
            elif not near and in_bridge_zone:
                if saved_state:
                    ConsoleLog("BridgeGuard",
                               f"leaving bridge zone ({int(px)},{int(py)}) — restoring custom behaviours",
                               Py4GW.Console.MessageType.Info)
                    try:
                        bot.Properties.ApplyNow("use_custom_behaviors", "active", True)
                    except Exception as e:
                        ConsoleLog("BridgeGuard", f"restore error: {e}",
                                   Py4GW.Console.MessageType.Error)
                in_bridge_zone = False
                saved_state = None
        except Exception as e:
            ConsoleLog("BridgeGuard", f"guard error: {e}", Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(POLL_MS)


def UmbralGrottoResetGuard(bot: Botting):
    """Reset to start of PrepareForCombat if bot returns to Umbral Grotto (639)
    after having left it once. Catches the death-teleport-back-to-outpost case
    where the FSM keeps marching but the player is in the wrong map.

    State machine:
      - has_left_grotto starts False
      - When map_id != 639 (and ready), flip to True
      - When map_id == 639 AND has_left_grotto, JumpToStepName start step
      - After reset, flip has_left_grotto back to False
    """
    UMBRAL_GROTTO = 639
    RESET_STEP = "[H]Enable Combat Mode_1"
    POLL_MS = 5000
    has_left_grotto = False
    while True:
        try:
            from Py4GWCoreLib import Map
            if Map.IsMapReady() and not Map.IsMapLoading():
                try:
                    current_map = int(Map.GetMapID())
                except Exception:
                    current_map = -1
                if current_map > 0:
                    if current_map != UMBRAL_GROTTO:
                        has_left_grotto = True
                    elif has_left_grotto:
                        ConsoleLog("UmbralReset",
                                   f"Detected return to Umbral Grotto ({UMBRAL_GROTTO}) — resetting to '{RESET_STEP}'",
                                   Py4GW.Console.MessageType.Warning)
                        try:
                            bot.States.JumpToStepName(RESET_STEP)
                        except Exception as e:
                            ConsoleLog("UmbralReset", f"jump error: {e}",
                                       Py4GW.Console.MessageType.Error)
                        has_left_grotto = False
        except Exception as e:
            ConsoleLog("UmbralReset", f"guard error: {e}", Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(POLL_MS)


def StuckStepWatchdog(bot: Botting):
    """Force-advance FSM if same step is active for >180s.

    SAFETY: After MAX_FORCED_ADVANCES consecutive force-skips, sends /resign
    instead of cascading deeper. Without this, a single bad state can chain
    into 10+ skipped states ending in 'On Unmanaged Fail' (saw this last night
    when XY_6 → XYAndInteractNPC_11 → ... → InteractWithGadgetXY: No target).

    The forced-advance counter resets when the FSM transitions naturally
    (state-change without our intervention).
    """
    STUCK_TIMEOUT_S = 180
    POLL_S = 5
    MAX_FORCED_ADVANCES = 2

    last_step_name = None
    last_step_change = time.time()
    forced_count = 0
    just_forced = False
    while True:
        try:
            fsm = bot.config.FSM
            current_state = getattr(fsm, "current_state", None)
            if current_state is None:
                yield from Routines.Yield.wait(2000)
                continue
            current_name = getattr(current_state, "name", "?")
            now = time.time()
            if current_name != last_step_name:
                last_step_name = current_name
                last_step_change = now
                # Reset forced counter only when state changed naturally,
                # NOT when we just force-advanced (which also produces a state change)
                if not just_forced:
                    forced_count = 0
                just_forced = False
            elif (now - last_step_change) >= STUCK_TIMEOUT_S:
                elapsed = int(now - last_step_change)

                # Cascade safety: stop force-skipping after 2 consecutive
                if forced_count >= MAX_FORCED_ADVANCES:
                    ConsoleLog("StuckSkip",
                               f"Stuck '{current_name}' {elapsed}s — already force-advanced "
                               f"{forced_count}x — /resign to escape cascade",
                               Py4GW.Console.MessageType.Error)
                    try:
                        Player.SendChatCommand("resign")
                    except Exception:
                        pass
                    forced_count = 0
                    just_forced = False
                    last_step_change = time.time()
                    last_step_name = None
                    yield from Routines.Yield.wait(5000)
                    continue

                next_state = getattr(current_state, "next_state", None)
                if next_state is None:
                    ConsoleLog("StuckSkip",
                               f"Stuck '{current_name}' {elapsed}s — no next_state, cannot advance",
                               Py4GW.Console.MessageType.Error)
                else:
                    next_name = getattr(next_state, "name", "?")
                    ConsoleLog("StuckSkip",
                               f"Stuck '{current_name}' {elapsed}s >= {STUCK_TIMEOUT_S}s — "
                               f"advancing to '{next_name}' (forced {forced_count+1}/{MAX_FORCED_ADVANCES})",
                               Py4GW.Console.MessageType.Warning)
                    try:
                        current_state.exit()
                    except Exception:
                        pass
                    fsm.current_state = next_state
                    try:
                        fsm.current_state.reset()
                        fsm.current_state.enter()
                    except Exception as e:
                        ConsoleLog("StuckSkip", f"transition enter() error: {e}",
                                   Py4GW.Console.MessageType.Error)
                    forced_count += 1
                    just_forced = True
                last_step_change = time.time()
                last_step_name = None
        except Exception as e:
            ConsoleLog("StuckSkip", f"watchdog error: {e}", Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(POLL_S * 1000)


def PrepareForCombat(bot: Botting) -> None:
    bot.States.AddHeader("Enable Combat Mode")
    bot.States.AddManagedCoroutine("StuckStepWatchdog", lambda: StuckStepWatchdog(bot))
    bot.States.AddManagedCoroutine("UmbralGrottoResetGuard", lambda: UmbralGrottoResetGuard(bot))
    bot.States.AddManagedCoroutine("BridgeApproachGuard", lambda: BridgeApproachGuard(bot))
    bot.States.AddManagedCoroutine("StuckBridgeWatchdog", lambda: StuckBridgeWatchdog(bot))
    bot.States.AddManagedCoroutine("CombatLootToggle", lambda: CombatLootToggle(bot))
    bot.States.AddManagedCoroutine("MoraleBoostWatchdog", lambda: MoraleBoostWatchdog(bot))
    _load_behavior_setting(bot)
    bot.Templates.Multibox_Aggressive()
    bot.States.AddCustomState(lambda: _apply_behavior_mode_step(bot), "Apply Behavior Mode")
    _sync_consumable_toggles(bot)
    bot.Multibox.LeavePartyOnAllAccounts()
    bot.Templates.Routines.PrepareForFarm(map_id_to_travel=639)
    bot.States.AddCustomState(lambda: _restock_consumables_if_enabled(bot), "Restock Consumables If Enabled")
    bot.Party.SetHardMode(True)


def Snowman(bot: Botting):
    #events
    condition = lambda: OnPartyWipe(bot)
    bot.Events.OnPartyWipeCallback(condition)
    #end events
    bot.States.AddHeader("Start Combat")
    bot.Move.XYAndDialog(-23884, 13954, 0x84)
    bot.Wait.ForMapToChange(target_map_id=701)
    bot.States.AddCustomState(lambda: _use_consumables_if_enabled(bot), "Use Consumables If Enabled")
    bot.States.AddManagedCoroutine("Upkeep Multibox Consumables", lambda: _upkeep_multibox_consumables(bot))
    bot.Move.XYAndInteractNPC(-14078.00, 15449.00)
    bot.Multibox.SendDialogToTarget(0x84)
    bot.Move.XY(-14804, 10703)
    bot.Move.XY(-15628, 9589)
    bot.Move.XY(-17602, 6858)
    bot.Wait.ForTime(1000)
    bot.Move.XY(-19769, 5046)
    bot.Move.XY(-16697.96, 1302.89)
    # bot.Move.XY(-14673.79, 2621.35) # Default
    bot.Move.XY(-15090.34, 2057.10) # Updated to avoid agroing both corridor and bridge groups
    bot.Move.XYAndInteractNPC(-12482.00, 3924.00)
    bot.Multibox.SendDialogToTarget(0x84)
    # Post-NPC detour around the (-14440 to -14510, 3400-3650) stuck-cluster.
    # Path-finder still routed back to the cluster with the previous x=-14550
    # detour (BridgeUnstuck logged "stuck at -14510, 3570"). Push detour ~250u
    # further west to x=-14800.
    bot.Move.XY(-14820, 3930)
    bot.Move.XY(-14800, 3640)
    bot.Move.XY(-14770, 2517)
    bot.Move.XY(-13824.00, 924.00)
    bot.Move.XY(-13752.06, -504.66)
    bot.Move.XY(-12084.77, -1592.58)
    bot.Move.XY(-12745.70, -3899.97)
    bot.Move.XY(-13262.00, -7346.00)
    bot.Move.XY(-14891.95, -10069.69)
    bot.Move.XY(-9573.00, -10963.00)
    bot.Move.FollowPath([(-9703.92, -10948.97)])
    bot.Wait.UntilOutOfCombat()
    bot.Items.LootItems()
    bot.Move.XYAndInteractNPC(-16093.00, -10723.00)
    bot.Multibox.SendDialogToTarget(0x84)
    bot.Move.XY(-15756.00, -12335.00)
    bot.Interact.WithGadgetAtXY(-15435.00, -12277.00) #lock
    bot.Wait.ForTime(3000)
    bot.Move.XY(-17542.00, -14048.00)
    bot.Move.XY(-13088.00, -17749.00)
    bot.Move.XY(-13004.20, -17304.91)
    bot.Wait.UntilOutOfCombat()
    bot.Items.LootItems()
    bot.Move.XY(-11136.00, -18043.00)
    bot.Interact.WithGadgetAtXY(-11136.00, -18043.00) #boss lock
    bot.Wait.ForTime(3000)
    bot.Move.XY(-7422.59, -18622.13)
    bot.Wait.ForTime(60000)
    bot.Interact.WithGadgetAtXY(-7594.00, -18657.00)
    bot.Items.LootItems()
    bot.Wait.ForTime(5000)
    bot.Multibox.ResignParty()
    bot.Wait.ForMapToChange(target_map_id=639)
    bot.States.JumpToStepName("[H]Enable Combat Mode_1")


bot.UI.override_draw_config(lambda: _draw_settings(bot))

bot.SetMainRoutine(Routine)
# endregion


# region Consumables
def _restock_consumables_if_enabled(bot: Botting):
    _sync_consumable_toggles(bot)
    if _as_bool(bot.Properties.Get("use_conset", "active")):
        yield from _restock_models_locally(CONSET_RESTOCK_MODELS, 250)
        yield from bot.helpers.Multibox._restock_conset_message(250)
    if _as_bool(bot.Properties.Get("use_pcons", "active")):
        yield from _restock_models_locally(PCON_RESTOCK_MODELS, 250)
        yield from bot.helpers.Multibox._restock_all_pcons_message(250)


def _use_consumables_if_enabled(bot: Botting):
    _sync_consumable_toggles(bot)
    if _as_bool(bot.Properties.Get("use_conset", "active")):
        for model_id, skill_name in CONSET_ITEMS:
            yield from bot.helpers.Multibox._use_consumable_message(
                (model_id, GLOBAL_CACHE.Skill.GetID(skill_name), 0, 0))
    if _as_bool(bot.Properties.Get("use_pcons", "active")):
        for model_id, skill_name in PCON_ITEMS:
            yield from bot.helpers.Multibox._use_consumable_message(
                (model_id, GLOBAL_CACHE.Skill.GetID(skill_name), 0, 0))


def _restock_models_locally(model_ids: list[int], quantity: int):
    for model_id in model_ids:
        yield from Routines.Yield.Items.RestockItems(model_id, quantity)
# endregion


# region Upkeep
def _upkeep_multibox_consumables(bot: "Botting"):
    from Py4GWCoreLib import Map as _UpMap
    def _explorable_safe() -> bool:
        # Per-iteration guard. Without this, /resign mid-cycle let UseItem
        # fire during the loading screen, freezing the master client.
        try:
            return (_UpMap.IsMapReady()
                    and not _UpMap.IsMapLoading()
                    and not _UpMap.IsOutpost()
                    and Routines.Checks.Map.MapValid())
        except Exception:
            return False
    while True:
        yield from bot.Wait._coro_for_time(15000)
        if not _explorable_safe():
            continue
        if _as_bool(bot.Properties.Get("use_conset", "active")):
            for model_id, skill_name in CONSET_ITEMS:
                if not _explorable_safe():
                    break
                yield from bot.helpers.Multibox._use_consumable_message(
                    (model_id, GLOBAL_CACHE.Skill.GetID(skill_name), 0, 0))
        if _as_bool(bot.Properties.Get("use_pcons", "active")):
            for model_id, skill_name in PCON_ITEMS:
                if not _explorable_safe():
                    break
                yield from bot.helpers.Multibox._use_consumable_message(
                    (model_id, GLOBAL_CACHE.Skill.GetID(skill_name), 0, 0))
            for _ in range(4):
                if not _explorable_safe():
                    break
                GLOBAL_CACHE.Inventory.UseItem(ModelID.Honeycomb.value)
                yield from bot.Wait._coro_for_time(250)
# endregion


# region Events
def _on_party_wipe(bot: "Botting"):
    while Agent.IsDead(Player.GetAgentID()):
        yield from bot.Wait._coro_for_time(1000)
        if not Routines.Checks.Map.MapValid():
            # Map invalid → release FSM and exit
            bot.config.FSM.resume()
            return

    # Player revived on same map → jump to recovery step
    bot.States.JumpToStepName("[H]Start Combat_2")
    bot.config.FSM.resume()


def OnPartyWipe(bot: "Botting"):
    ConsoleLog("on_party_wipe", "event triggered")
    fsm = bot.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnWipe_OPD", lambda: _on_party_wipe(bot))
# endregion


# region Settings
def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _ensure_bot_ini(bot: Botting) -> str:
    if not bot.config.ini_key_initialized:
        bot.config.ini_key = IniManager().ensure_key(
            f"BottingClass/bot_{bot.config.bot_name}",
            f"bot_{bot.config.bot_name}.ini",
        )
        bot.config.ini_key_initialized = True
    return bot.config.ini_key


def _load_behavior_setting(bot: Botting) -> None:
    ini_key = _ensure_bot_ini(bot)
    if not ini_key:
        return
    # Default = True (the bot's intended setup, see line 17 Property active=True).
    # NOT the current property value — that leaks any stale False from a prior
    # run's BridgeApproachGuard that didn't restore (e.g., script crashed
    # mid-bridge-zone). Without this default, _apply_behavior_mode broadcasts
    # the stale False to all 8 accounts, disabling custom_behaviors fleet-wide.
    saved_value = IniManager().read_bool(
        ini_key,
        _SETTINGS_SECTION,
        _BEHAVIOR_MODE_KEY,
        True,
    )
    bot.Properties.ApplyNow("use_custom_behaviors", "active", _as_bool(saved_value))


def _save_behavior_setting(bot: Botting) -> None:
    ini_key = _ensure_bot_ini(bot)
    if not ini_key:
        return
    IniManager().write_key(
        ini_key,
        _SETTINGS_SECTION,
        _BEHAVIOR_MODE_KEY,
        _as_bool(bot.Properties.Get("use_custom_behaviors", "active")),
    )


def _sync_consumable_toggles(bot: Botting) -> None:
    use_conset = _as_bool(bot.Properties.Get("use_conset", "active"))
    use_pcons = _as_bool(bot.Properties.Get("use_pcons", "active"))

    for key in ("armor_of_salvation", "essence_of_celerity", "grail_of_might"):
        bot.Properties.ApplyNow(key, "active", use_conset)

    for key in (
        "birthday_cupcake",
        "golden_egg",
        "candy_corn",
        "candy_apple",
        "slice_of_pumpkin_pie",
        "drake_kabob",
        "bowl_of_skalefin_soup",
        "pahnai_salad",
        "war_supplies",
        "honeycomb",
    ):
        bot.Properties.ApplyNow(key, "active", use_pcons)


def _apply_behavior_mode(bot: Botting) -> None:
    use_custom_behaviors = _as_bool(bot.Properties.Get("use_custom_behaviors", "active"))
    from Py4GW_widget_manager import get_widget_handler
    widget_handler = get_widget_handler()
    custom_behavior_party = None
    try:
        from Sources.oazix.CustomBehaviors.primitives.parties.custom_behavior_party import CustomBehaviorParty
        custom_behavior_party = CustomBehaviorParty()
    except Exception:
        custom_behavior_party = None
    if use_custom_behaviors:
        bot.Properties.Disable("hero_ai")
        if custom_behavior_party is not None:
            custom_behavior_party.set_party_is_enabled(True)
        if not widget_handler.is_widget_enabled("CustomBehaviors"):
            widget_handler.enable_widget("CustomBehaviors")
        if widget_handler.is_widget_enabled("HeroAI"):
            widget_handler.disable_widget("HeroAI")
        bot.Multibox.ApplyWidgetPolicy(enable_widgets=('CustomBehaviors',), disable_widgets=('HeroAI',), apply_local=False)
    else:
        bot.Properties.Enable("hero_ai")
        if custom_behavior_party is not None:
            custom_behavior_party.set_party_is_enabled(False)
        if widget_handler.is_widget_enabled("CustomBehaviors"):
            widget_handler.disable_widget("CustomBehaviors")
        if not widget_handler.is_widget_enabled("HeroAI"):
            widget_handler.enable_widget("HeroAI")
        bot.Multibox.ApplyWidgetPolicy(enable_widgets=('HeroAI',), disable_widgets=('CustomBehaviors',), apply_local=False)


def _apply_behavior_mode_step(bot: Botting):
    """Generator wrapper so AddCustomState defers the behavior-mode apply until
    the FSM actually reaches this step. Calling _apply_behavior_mode(bot) at
    FSM-build time fires it once at script load (the CustomBehaviorParty
    timing bug). This wrapper runs it at the correct moment."""
    _apply_behavior_mode(bot)
    yield
# endregion


# region GUI
def _draw_settings(bot: Botting):
    import PyImGui

    PyImGui.text("Bot Settings")

    _load_behavior_setting(bot)
    use_custom_behaviors = _as_bool(bot.Properties.Get("use_custom_behaviors", "active"))
    use_hero_ai = not use_custom_behaviors
    new_use_hero_ai          = PyImGui.checkbox("Use Hero AI",          use_hero_ai)
    new_use_custom_behaviors = PyImGui.checkbox("Use Custom Behaviors", use_custom_behaviors)
    if new_use_hero_ai != use_hero_ai:
        desired = not new_use_hero_ai
    elif new_use_custom_behaviors != use_custom_behaviors:
        desired = new_use_custom_behaviors
    else:
        desired = use_custom_behaviors
    if desired != use_custom_behaviors:
        bot.Properties.ApplyNow("use_custom_behaviors", "active", desired)
        _save_behavior_setting(bot)
        _apply_behavior_mode(bot)

    use_conset = _as_bool(bot.Properties.Get("use_conset", "active"))
    use_conset = PyImGui.checkbox("Restock & use Conset", use_conset)
    bot.Properties.ApplyNow("use_conset", "active", use_conset)

    use_pcons = _as_bool(bot.Properties.Get("use_pcons", "active"))
    use_pcons = PyImGui.checkbox("Restock & use Pcons", use_pcons)
    bot.Properties.ApplyNow("use_pcons", "active", use_pcons)
    _sync_consumable_toggles(bot)


def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Deldrimor Title Farm", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    # Description
    PyImGui.text("Multi Account, farm Deldrimor title in Slavers' Exile")
    PyImGui.spacing()
    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Wick Divinus")
    PyImGui.end_tooltip()


_session_baselines: dict[str, int] = {}
_session_start_times: dict[str, float] = {}


def _draw_title_track():
    global _session_baselines, _session_start_times
    import PyImGui
    title_idx = int(TitleID.Deldrimor)
    tiers = TITLE_TIERS.get(TitleID.Deldrimor, [])
    now = time.time()
    for account in GLOBAL_CACHE.ShMem.GetAllAccountData():
        name = account.AgentData.CharacterName
        pts = account.TitlesData.Titles[title_idx].CurrentPoints
        if name not in _session_baselines:
            _session_baselines[name] = pts
            _session_start_times[name] = now
        tier_name = "Unranked"
        tier_rank = 0
        prev_required = 0
        next_required = tiers[0].required if tiers else 0
        for i, tier in enumerate(tiers):
            if pts >= tier.required:
                tier_name = tier.name
                tier_rank = i + 1
                prev_required = tier.required
                next_required = tiers[i + 1].required if i + 1 < len(tiers) else tier.required
            else:
                next_required = tier.required
                break
        is_maxed = tiers and pts >= tiers[-1].required
        PyImGui.separator()
        PyImGui.text(f"{name}  [{tier_name} (Rank {tier_rank})]")
        if is_maxed:
            PyImGui.text_colored("Maximum rank achieved. Title complete.", (0.4, 1.0, 0.4, 1.0))
            continue
        gained = pts - _session_baselines[name]
        elapsed = now - _session_start_times[name]
        pts_hr = int(gained / elapsed * 3600) if elapsed > 0 else 0
        PyImGui.text(f"Points: {pts:,} / {next_required:,}")
        if next_required > prev_required:
            frac = min((pts - prev_required) / (next_required - prev_required), 1.0)
            PyImGui.progress_bar(frac, -1, 0, f"{pts - prev_required:,} / {next_required - prev_required:,}")
        PyImGui.text(f"+{gained:,}  ({pts_hr:,}/hr)")


REFORGED_TEXTURE = os.path.join(Py4GW.Console.get_projects_path(), "Sources", "Wick Divinus bots", "Reforged_Icon.png")
# endregion


# region Entry Point
def main():
    bot.Update()
    bot.UI.draw_window(icon_path=REFORGED_TEXTURE, extra_tabs=[("Statistics", _draw_title_track)])


if __name__ == "__main__":
    main()
# endregion
