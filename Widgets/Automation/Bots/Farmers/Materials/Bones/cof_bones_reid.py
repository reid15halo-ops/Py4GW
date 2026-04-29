# region imports
from Py4GWCoreLib import *
import time
# endregion

MODULE_NAME = "CoF Bones by REID"
MODULE_ICON = "Textures\\Module_Icons\\CoF Bone Farmer.png"

# region constants
BOT_NAME = "CoF Bones by REID"
LOG_CHANNEL = "CoF-REID"

DOOMLORE_SHRINE_MAP_ID = 648
COF_MAP_ID = 560

# Skill slots (must match build template order)
SLOT_SOMS = 1  # Signet of Mystic Speed
SLOT_PF = 2    # Pious Fury (used to strip GA for health steal)
SLOT_GA = 3    # Grenth's Aura
SLOT_VOS = 4   # Vow of Silence
SLOT_CV = 5    # Crippling Victory
SLOT_RI = 6    # Reap Impurities
SLOT_VOP = 7   # Vow of Piety
SLOT_IAU = 8   # "I Am Unstoppable!"

BUILD_TEMPLATE_IAU = 'OgCjwqpq6SYiihdftXjhOXhX0k'

# Coordinates
NPC_XY = (-19166.00, 17980.00)
QUEST_DIALOG = 0x832101
DUNGEON_DIALOG = 0x88

PREP_SPOT = (-16623, -8989)
KILL_SPOT = (-15654, -8966)
GROUP2_SPOT = (-15743, -7786)

# endregion

# region run stats
run_stats = {
    "runs": 0,
    "fails": 0,
    "bones": 0,
    "run_start_ts": 0.0,
    "total_start_ts": 0.0,
    "lapping": False,
}

_resign_in_progress = False
# endregion


# region logging
def _log(msg: str, level="Info"):
    msg_type = Py4GW.Console.MessageType.Info
    if level == "Warning":
        msg_type = Py4GW.Console.MessageType.Warning
    elif level == "Error":
        msg_type = Py4GW.Console.MessageType.Error
    elif level == "Success":
        msg_type = Py4GW.Console.MessageType.Success
    Py4GW.Console.Log(LOG_CHANNEL, str(msg), msg_type)
# endregion


# region condition functions
def is_at_kill_spot():
    px, py = Player.GetXY()
    return Utils.Distance((px, py), KILL_SPOT) < 200


def is_enemies_dead_nearby():
    enemy_array = AgentArray.GetEnemyArray()
    enemy_array = AgentArray.Filter.ByAttribute(enemy_array, 'IsAlive')
    enemy_array = AgentArray.Filter.ByDistance(enemy_array, Player.GetXY(), 1200)
    return len(enemy_array) == 0


def is_combat_done():
    """Check if all nearby enemies are dead or only stragglers remain."""
    enemy_array = AgentArray.GetEnemyArray()
    enemy_array = AgentArray.Filter.ByAttribute(enemy_array, 'IsAlive')
    enemy_array = AgentArray.Filter.ByDistance(enemy_array, Player.GetXY(), 600)
    if not enemy_array:
        return True
    if len(enemy_array) < 2 and Agent.GetHealth(enemy_array[0]) > 0.4:
        return True
    return False


def is_loot_collected():
    """Check if all loot nearby has been picked up."""
    try:
        lc = LootConfig()
        loot_array = lc.GetfilteredLootArray(
            distance=Range.SafeCompass.value,
            multibox_loot=False,
            allow_unasigned_loot=True,
        )
        return len(loot_array) == 0
    except:
        return True


def is_group2_alive():
    """Check if group 2 enemies still exist."""
    enemy_array = AgentArray.GetEnemyArray()
    enemy_array = AgentArray.Filter.ByAttribute(enemy_array, 'IsAlive')
    enemy_array = AgentArray.Filter.ByDistance(enemy_array, Player.GetXY(), 2000)
    return len(enemy_array) > 0
# endregion


# region custom state functions
def _mark_run_start():
    global run_stats
    run_stats["runs"] += 1
    run_stats["run_start_ts"] = time.time()
    if run_stats["total_start_ts"] == 0:
        run_stats["total_start_ts"] = time.time()
    _log(f"=== RUN #{run_stats['runs']} START ===")


def _mark_run_success():
    elapsed = time.time() - run_stats["run_start_ts"]
    _log(f"RUN #{run_stats['runs']} COMPLETE in {elapsed:.0f}s | fails: {run_stats['fails']}", "Success")


def _mark_run_fail():
    run_stats["fails"] += 1
    _log(f"RUN #{run_stats['runs']} FAILED (total fails: {run_stats['fails']})", "Warning")


def _load_skillbar():
    GLOBAL_CACHE.SkillBar.LoadSkillTemplate(BUILD_TEMPLATE_IAU)
    _log("Skillbar loaded")


def _set_normal_mode():
    GLOBAL_CACHE.Party.SetNormalMode()


def _request_enemy_names():
    enemy_array = AgentArray.GetEnemyArray()
    for enemy in enemy_array:
        Agent.RequestName(enemy)


def _set_resign_flag():
    global _resign_in_progress
    _resign_in_progress = True


def _clear_resign_flag():
    global _resign_in_progress
    _resign_in_progress = False


def _resign():
    Player.SendChatCommand('resign')


def _return_to_outpost():
    GLOBAL_CACHE.Party.ReturnToOutpost()
# endregion


# region watchdog coroutine
def step_watchdog():
    """Per-step watchdog: forces resign if any step hangs >120s."""
    last_step_name = None
    step_started_ts = time.time()
    STEP_TIMEOUT_S = 120.0

    while True:
        yield from Routines.Yield.wait(2000)
        try:
            fsm = bot.config.FSM
            current_name = fsm.get_current_step_name()
        except:
            continue

        if current_name != last_step_name:
            last_step_name = current_name
            step_started_ts = time.time()
            continue

        elapsed = time.time() - step_started_ts
        if elapsed >= STEP_TIMEOUT_S * 0.75:
            _log(f"WATCHDOG WARN: '{current_name}' active {elapsed:.0f}s", "Warning")

        if elapsed >= STEP_TIMEOUT_S:
            _log(f"WATCHDOG TIMEOUT: '{current_name}' — forcing resign", "Error")
            _mark_run_fail()
            try:
                Player.SendChatCommand('resign')
            except:
                pass
            step_started_ts = time.time()
# endregion


# region death handler coroutine
def death_monitor():
    """Background monitor: detect death and handle recovery."""
    while True:
        yield from Routines.Yield.wait(500)
        try:
            if not Map.IsExplorable() or not Map.IsMapReady():
                continue
            if not Agent.IsDead(Player.GetAgentID()):
                continue

            fsm = bot.config.FSM
            current = fsm.get_current_step_name()

            # Don't interfere during resign/return
            if _resign_in_progress:
                continue
            if 'resign' in current.lower() or 'return' in current.lower():
                continue

            _log(f"DIED during '{current}' — jumping to resign", "Warning")
            _mark_run_fail()
            fsm.jump_to_state_by_name("Resign")
        except:
            continue
# endregion


# region VoS monitor coroutine
def vos_emergency_recast():
    """Background monitor: recast VoS immediately if it drops during combat/movement."""
    while True:
        yield from Routines.Yield.wait(300)
        try:
            if not Map.IsExplorable() or not Map.IsMapReady():
                continue
            if Agent.IsDead(Player.GetAgentID()):
                continue

            # Only during dangerous states
            fsm = bot.config.FSM
            current = fsm.get_current_step_name()
            dangerous_states = ['Kill Route', 'Wait for enemies', 'Kill enemies', 'Pull group 2']
            if not any(s in current for s in dangerous_states):
                continue

            # Check VoS
            vos_time = 0
            for effect in Effects.GetEffects(Player.GetAgentID()):
                if effect.skill_id == 1517:
                    vos_time = effect.time_remaining
                    break

            if vos_time > 0:
                continue  # VoS still active

            # VoS dropped — recast if we can
            energy = int(Agent.GetEnergy(Player.GetAgentID()) * Agent.GetMaxEnergy(Player.GetAgentID()))
            skill_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_VOS)
            if skill_data.recharge == 0 and energy >= 10:
                yield from Routines.Yield.Skills.CastSkillSlot(SLOT_VOS, aftercast_delay=100)
        except:
            continue
# endregion


# region combat coroutine
def combat_rotation():
    """Background combat rotation: runs during kill states."""
    while True:
        yield from Routines.Yield.wait(250)
        try:
            if not Map.IsExplorable() or not Map.IsMapReady():
                continue
            if Agent.IsDead(Player.GetAgentID()):
                continue

            fsm = bot.config.FSM
            current = fsm.get_current_step_name()
            if 'Kill enemies' not in current and 'Kill group 2' not in current:
                continue

            player_id = Player.GetAgentID()
            player_xy = Player.GetXY()

            # Check VoS status
            vos_active = False
            for effect in Effects.GetEffects(player_id):
                if effect.skill_id == 1517:
                    vos_active = True
                    break

            # If VoS is down, recast chain: GA -> VoS
            if not vos_active:
                energy = int(Agent.GetEnergy(player_id) * Agent.GetMaxEnergy(player_id))
                # LOW energy: VoS only
                if energy >= 10:
                    vos_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_VOS)
                    if vos_data.recharge == 0:
                        # Try GA first if enough energy
                        if energy >= 20:
                            ga_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_GA)
                            if ga_data.recharge == 0:
                                yield from Routines.Yield.Skills.CastSkillSlot(SLOT_GA, aftercast_delay=100)
                                yield from Routines.Yield.wait(200)
                        # Cast VoS
                        yield from Routines.Yield.Skills.CastSkillSlot(SLOT_VOS, aftercast_delay=100)
                continue

            # VoS is active — use non-spell skills only

            # SoMS (signet)
            soms_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_SOMS)
            if soms_data.recharge == 0:
                soms_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(SLOT_SOMS)
                soms_active = Effects.BuffExists(player_id, soms_id) or Effects.EffectExists(player_id, soms_id)
                if not soms_active:
                    yield from Routines.Yield.Skills.CastSkillSlot(SLOT_SOMS, aftercast_delay=50)

            # IaU (shout)
            iau_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_IAU)
            if iau_data.recharge == 0:
                iau_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(SLOT_IAU)
                iau_active = Effects.BuffExists(player_id, iau_id) or Effects.EffectExists(player_id, iau_id)
                if not iau_active:
                    yield from Routines.Yield.Skills.CastSkillSlot(SLOT_IAU, aftercast_delay=50)

            # Target nearest enemy
            target_id = Player.GetTargetID()
            needs_target = (target_id == 0
                or Agent.GetAllegiance(target_id)[0] != 3
                or Agent.IsDead(target_id)
                or Utils.Distance(Agent.GetXY(target_id), player_xy) > 350)

            if needs_target:
                enemy_array = AgentArray.GetEnemyArray()
                enemy_array = AgentArray.Filter.ByAttribute(enemy_array, 'IsAlive')
                enemy_array = AgentArray.Filter.ByDistance(enemy_array, player_xy, 350)
                enemy_array = AgentArray.Sort.ByDistance(enemy_array, player_xy)
                if enemy_array:
                    Player.ChangeTarget(enemy_array[0])
                    yield from Routines.Yield.wait(200)
                continue

            # Attack
            if not Agent.IsAttacking(player_id):
                Keystroke.PressAndRelease(Key.Space.value)
                yield from Routines.Yield.wait(400)

            # Adrenaline skills (CV, RI)
            for slot in [SLOT_CV, SLOT_RI]:
                skill_data = GLOBAL_CACHE.SkillBar.GetSkillData(slot)
                skill_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(slot)
                if skill_data.adrenaline_a >= Skill.Data.GetAdrenaline(skill_id):
                    yield from Routines.Yield.Skills.CastSkillSlot(slot, aftercast_delay=100)
                    yield from Routines.Yield.wait(300)
        except:
            continue
# endregion


# region prep coroutine
def prep_and_engage():
    """Cast buffs, equip scythe, cast VoS, then yield."""
    player_id = Player.GetAgentID()

    # Cast VoP (safe, no enchant removal) if not active
    vop_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(SLOT_VOP)
    if not Effects.BuffExists(player_id, vop_id) and not Effects.EffectExists(player_id, vop_id):
        vop_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_VOP)
        if vop_data.recharge == 0:
            yield from Routines.Yield.Skills.CastSkillSlot(SLOT_VOP, aftercast_delay=100)

    # Cast GA if not active
    ga_id = GLOBAL_CACHE.SkillBar.GetSkillIDBySlot(SLOT_GA)
    if not Effects.BuffExists(player_id, ga_id) and not Effects.EffectExists(player_id, ga_id):
        ga_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_GA)
        if ga_data.recharge == 0:
            yield from Routines.Yield.Skills.CastSkillSlot(SLOT_GA, aftercast_delay=100)

    # Equip scythe (weapon set 1)
    Keystroke.PressAndRelease(Key.F1.value)
    yield from Routines.Yield.wait(500)

    # Clear target
    Player.ChangeTarget(0)
    yield from Routines.Yield.wait(200)

    # Cast VoS last (blocks spells)
    vos_data = GLOBAL_CACHE.SkillBar.GetSkillData(SLOT_VOS)
    if vos_data.recharge == 0:
        yield from Routines.Yield.Skills.CastSkillSlot(SLOT_VOS, aftercast_delay=100)

    _log("Buffs applied, scythe equipped, VoS cast — engaging")
# endregion


# region wait for enemies to settle
def wait_for_enemies_settle():
    """Wait until 8+ enemies within 500 range or timeout after 6s."""
    start = time.time()
    while True:
        if Agent.IsDead(Player.GetAgentID()):
            return
        if Agent.GetHealth(Player.GetAgentID()) < 0.5:
            return
        if time.time() - start > 6.0:
            return

        enemy_array = AgentArray.GetEnemyArray()
        enemy_array = AgentArray.Filter.ByAttribute(enemy_array, 'IsAlive')
        enemy_array = AgentArray.Filter.ByDistance(enemy_array, Player.GetXY(), 500)
        if len(enemy_array) >= 8:
            return

        yield from Routines.Yield.wait(200)
# endregion


# region wait for kill complete
def wait_for_kill_complete():
    """Wait until all nearby enemies are dead."""
    while True:
        if Agent.IsDead(Player.GetAgentID()):
            _mark_run_fail()
            return
        if is_combat_done():
            return
        yield from Routines.Yield.wait(500)
# endregion


# region main farm routine
def farm_bones(bot_instance: Botting) -> None:
    bot_instance.States.AddHeader(BOT_NAME)

    # Template: solo aggressive (no heroes)
    bot_instance.Templates.Aggressive(
        pause_on_danger=False,
        halt_on_death=False,
        auto_combat=False,  # We handle combat ourselves (VoS build)
        auto_loot=True,
    )

    # Background monitors
    bot_instance.States.AddManagedCoroutine('Step watchdog (120s)', step_watchdog)
    bot_instance.States.AddManagedCoroutine('Death monitor', death_monitor)
    bot_instance.States.AddManagedCoroutine('VoS emergency recast', vos_emergency_recast)
    bot_instance.States.AddManagedCoroutine('Combat rotation', combat_rotation)

    # ===== SETUP =====
    bot_instance.States.AddHeader("Setup")
    bot_instance.States.AddCustomState(_load_skillbar, "Load skillbar")
    bot_instance.Wait.ForTime(1000)
    bot_instance.States.AddCustomState(_set_normal_mode, "Set Normal Mode")
    bot_instance.Wait.ForTime(500)

    # ===== FARM LOOP =====
    bot_instance.States.AddHeader("Farm Loop")
    bot_instance.States.AddCustomState(_mark_run_start, "Mark run start")

    # Equip staff for movement
    bot_instance.States.AddCustomState(
        lambda: Keystroke.PressAndRelease(Key.F2.value),
        "Equip staff"
    )
    bot_instance.Wait.ForTime(500)

    # Go to NPC, take quest, enter dungeon
    bot_instance.Move.XY(NPC_XY[0], NPC_XY[1], step_name="Go to NPC")
    bot_instance.Dialogs.AtXY(NPC_XY[0], NPC_XY[1], dialog=QUEST_DIALOG, step_name="Take quest")
    bot_instance.Wait.ForTime(500)
    bot_instance.Dialogs.AtXY(NPC_XY[0], NPC_XY[1], dialog=DUNGEON_DIALOG, step_name="Enter dungeon")
    bot_instance.Wait.ForMapLoad(target_map_id=COF_MAP_ID)

    # Request enemy names for filtering
    bot_instance.States.AddCustomState(_request_enemy_names, "Request enemy names")

    # Move to prep spot
    bot_instance.Move.XY(PREP_SPOT[0], PREP_SPOT[1], step_name="Go to prep spot")
    bot_instance.Wait.ForTime(1000)

    # Prep skills + equip scythe + cast VoS
    bot_instance.States.AddCustomState(prep_and_engage, "Prep & engage")

    # Run to kill spot
    bot_instance.Move.XY(KILL_SPOT[0], KILL_SPOT[1], step_name="Kill Route")

    # Wait for enemies to ball up
    bot_instance.States.AddCustomState(wait_for_enemies_settle, "Wait for enemies")

    # Kill group 1
    bot_instance.States.AddCustomState(wait_for_kill_complete, "Kill enemies")

    # Check if group 2 exists, pull if needed
    bot_instance.States.AddCustomState(
        lambda: _log("Checking group 2...") if is_group2_alive() else None,
        "Check group 2"
    )

    # Pull group 2
    bot_instance.Move.XY(GROUP2_SPOT[0], GROUP2_SPOT[1], step_name="Pull group 2")
    bot_instance.Move.XY(KILL_SPOT[0], KILL_SPOT[1], step_name="Return to kill spot")
    bot_instance.States.AddCustomState(wait_for_enemies_settle, "Wait for group 2")
    bot_instance.States.AddCustomState(wait_for_kill_complete, "Kill group 2")

    # Loot
    bot_instance.Wait.UntilCondition(is_loot_collected, duration=500)
    bot_instance.States.AddCustomState(_mark_run_success, "Log run success")

    # Resign and return
    bot_instance.States.AddCustomState(_set_resign_flag, "Arm resign guard")
    bot_instance.States.AddCustomState(_resign, "Resign")
    bot_instance.Wait.ForTime(1000)
    bot_instance.Wait.UntilCondition(
        lambda: GLOBAL_CACHE.Party.IsPartyDefeated() or Agent.IsDead(Player.GetAgentID()),
        duration=500
    )
    bot_instance.Wait.ForTime(500)
    bot_instance.States.AddCustomState(_return_to_outpost, "Return to outpost")
    bot_instance.Wait.ForMapLoad(target_map_id=DOOMLORE_SHRINE_MAP_ID)
    bot_instance.States.AddCustomState(_clear_resign_flag, "Clear resign guard")
    bot_instance.Wait.ForTime(500)

    # Loop back to farm
    bot_instance.States.JumpToStepName("[H]Farm Loop_1")
# endregion


# region bot instance
bot = Botting(
    BOT_NAME,
    config_halt_on_death=False,
    config_movement_timeout=15000,
    config_movement_tolerance=150,
)
bot.SetMainRoutine(farm_bones)
# endregion


# region GUI
class Draw:
    def __init__(self):
        self.window_module = ImGui.WindowModule(
            'CoF Bones REID',
            window_name='CoF Bones by REID',
            window_pos=(234, 802),
            window_flags=PyImGui.WindowFlags.AlwaysAutoResize,
        )
        self.first_run = True

    def Run(self):
        if self.first_run:
            PyImGui.set_next_window_size(
                self.window_module.window_size[0],
                self.window_module.window_size[1],
            )
            PyImGui.set_next_window_pos(
                self.window_module.window_pos[0],
                self.window_module.window_pos[1],
            )
            self.first_run = False

        try:
            PyImGui.push_style_color(PyImGui.ImGuiCol.WindowBg, (0.0, 0.0, 0.0, 0.7))

            if PyImGui.begin(self.window_module.window_name, self.window_module.window_flags):
                # Start/Stop button
                window_width = 240
                button_width = window_width - 20
                if bot.config.fsm_running:
                    if PyImGui.button('\uf04d Stop', button_width, 25):
                        bot.Stop()
                else:
                    if PyImGui.button('\uf04b Start', button_width, 25):
                        bot.Start()

                # Status
                try:
                    fsm = bot.config.FSM
                    state = fsm.get_current_step_name() if bot.config.fsm_running else 'idle'
                except:
                    state = 'idle'
                PyImGui.text(f'State: {state}')

                # Stats table
                elapsed = time.time() - run_stats["total_start_ts"] if run_stats["total_start_ts"] > 0 else 0
                hours = int(elapsed // 3600)
                mins = int((elapsed % 3600) // 60)

                if PyImGui.begin_table('Stats', 2, PyImGui.TableFlags.Borders | PyImGui.TableFlags.RowBg):
                    for label, value in [
                        ('Runs', run_stats['runs']),
                        ('Fails', run_stats['fails']),
                        ('Runtime', f'{hours}h {mins}m'),
                    ]:
                        PyImGui.table_next_row()
                        PyImGui.table_next_column()
                        PyImGui.text(label)
                        PyImGui.table_next_column()
                        PyImGui.text(str(value))
                    PyImGui.end_table()

            PyImGui.end()
            PyImGui.pop_style_color(1)
        except Exception as e:
            Py4GW.Console.Log('CoF-REID', f'GUI error: {e}', Py4GW.Console.MessageType.Error)


draw = Draw()
# endregion


# region main
def main():
    try:
        if not Map.IsMapReady() or not GLOBAL_CACHE.Party.IsPartyLoaded():
            return

        draw.Run()
        bot.Update()

    except Exception as e:
        Py4GW.Console.Log('CoF-REID', f'Error: {e}', Py4GW.Console.MessageType.Error)
        import traceback
        Py4GW.Console.Log('CoF-REID', traceback.format_exc(), Py4GW.Console.MessageType.Error)


def tooltip():
    import PyImGui
    from Py4GWCoreLib import ImGui, Color
    PyImGui.begin_tooltip()
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("CoF Bones by REID", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.text("VoS Dervish bone farmer for Cathedral of Flames.")
    PyImGui.text("Built on Botting framework with managed coroutines.")
    PyImGui.spacing()
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("REID")
    PyImGui.bullet_text("Original by jtmele1")
    PyImGui.end_tooltip()


if __name__ == '__main__':
    main()
# endregion
