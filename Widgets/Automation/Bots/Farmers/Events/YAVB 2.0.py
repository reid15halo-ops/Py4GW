import Py4GW
import math

import PyImGui
from Py4GWCoreLib import (Routines,Botting,ActionQueueManager, ConsoleLog, GLOBAL_CACHE, Agent, Utils, ImGui, Color, ColorPalette)
from Py4GWCoreLib import ThrottledTimer, Map, Player
from Py4GWCoreLib.enums import ModelID, Range, TitleID

from Py4GWCoreLib.BuildMgr import BuildMgr

from Py4GWCoreLib.Builds.Mesmer.Me_A.SF_Mes_vaettir import SF_Mes_vaettir
from Py4GWCoreLib.Builds.Assassin.A_Me.SF_Ass_vaettir import SF_Ass_vaettir

from typing import List, Tuple

from Widgets.Automation.Helpers.FarmStats import FarmRunTracker

bot = Botting("YAVB 2.0",
              upkeep_birthday_cupcake_restock=1)

_yavb_tracker = FarmRunTracker("YAVB 2.0 Jaga Moraine")


def TrackStartRun(bot: Botting):
    _yavb_tracker.start_run(strategy="jaga_moraine", hard_mode=True)
    yield


def TrackEndRunSuccess(bot: Botting):
    if _yavb_tracker._run_active:
        _yavb_tracker.end_run(success=True)
    yield
  
MODULE_ICON = "Textures\\Module_Icons\\YAVB 2.0 mascot.png"

def create_bot_routine(bot: Botting) -> None:
    TownRoutines(bot)
    TraverseBjoraMarches(bot)
    JagaMoraineFarmRoutine(bot)
    ResetFarmLoop(bot)
    
def InitializeBot(bot: Botting) -> None:
    condition = lambda: on_death(bot)
    bot.Events.OnDeathCallback(condition)
    bot.States.AddHeader("Initialize Bot")
    bot.Properties.Disable("auto_inventory_management")
    bot.Properties.Disable("auto_loot")
    bot.Properties.Disable("hero_ai")
    bot.Properties.Enable("auto_combat")
    bot.Properties.Disable("pause_on_danger")
    bot.Properties.Enable("halt_on_death")
    bot.Properties.Set("movement_timeout",value=-1)
    bot.Properties.Enable("birthday_cupcake")
    bot.Properties.Enable("identify_kits")
    bot.Properties.Enable("salvage_kits")
    bot.Properties.Set("leave_empty_inventory_slots", value=10)
    
    # Configure loot settings directly via the LootConfig singleton.
    # NOTE: bot.Items.AddModelToLootBlacklist() does NOT work here because
    # it is a @_yield_step generator that is never executed when called
    # synchronously inside InitializeBot(). We must call LootConfig directly.
    from Py4GWCoreLib.py4gwcorelib_src.Lootconfig_src import LootConfig
    loot_cfg = LootConfig()
    
    # Set rarity filters: loot blues, purples, and golds (all salvageable drops)
    # Whites and greens are skipped to reduce clutter.
    loot_cfg.SetProperties(loot_whites=False, loot_blues=True, loot_purples=True, loot_golds=True, loot_greens=False, loot_gold_coins=True)
    
    # Blacklist map pieces, dyes, and festive items so they are never picked up
    _BLOCKED_MODELS = {
        ModelID.Map_Piece_Bottom_Left.value,
        ModelID.Map_Piece_Bottom_Right.value,
        ModelID.Map_Piece_Top_Left.value,
        ModelID.Map_Piece_Top_Right.value,
        ModelID.Vial_Of_Dye.value,
        # Festive items (tonics, fireworks, etc.) — non-salvageable clutter
        ModelID.Bottle_Rocket.value,
        ModelID.Champagne_Popper.value,
        ModelID.Ghost_In_The_Box.value,
        ModelID.Snowman_Summoner.value,
        ModelID.Sparkler.value,
        ModelID.Squash_Serum.value,
        ModelID.Beetle_Juice_Tonic.value,
        ModelID.Cottontail_Tonic.value,
        ModelID.Frosty_Tonic.value,
        ModelID.Mischievious_Tonic.value,
        ModelID.Sinister_Automatonic_Tonic.value,
        ModelID.Transmogrifier_Tonic.value,
        ModelID.Yuletide_Tonic.value,
        ModelID.Cerebral_Tonic.value,
        ModelID.Searing_Tonic.value,
        ModelID.Abyssal_Tonic.value,
        ModelID.Unseen_Tonic.value,
        ModelID.Phantasmal_Tonic.value,
        ModelID.Automatonic_Tonic.value,
        ModelID.Boreal_Tonic.value,
        ModelID.Trapdoor_Tonic.value,
        ModelID.Macabre_Tonic.value,
        ModelID.Skeletonic_Tonic.value,
        ModelID.Gelatinous_Tonic.value,
        ModelID.Abominable_Tonic.value,
        ModelID.Crate_Of_Fireworks.value,
        ModelID.Minutely_Mad_King_Tonic.value,
        ModelID.Zaishen_Tonic.value,
        ModelID.Mysterious_Tonic.value,
        ModelID.Disco_Ball.value,
        ModelID.Party_Beacon.value,
        ModelID.Spooky_Tonic.value,
    }
    for mid in _BLOCKED_MODELS:
        loot_cfg.AddToBlacklist(mid)
    
    # Also add a custom check as a safety net
    def _yavb_loot_filter(item_id: int):
        from Py4GWCoreLib.Item import Item
        model_id = Item.GetModelID(item_id)
        if model_id in _BLOCKED_MODELS:
            return False
        return None
    loot_cfg.AddCustomItemCheck(_yavb_loot_filter)

def DisconnectGuard(bot: Botting):
    """Monitors connection status and forces resign if the client disconnects.

    Three-poll requirement: skips during map load/transition AND requires 3
    consecutive 5s-spaced failed polls (=15s of continuous IsPlayerLoaded=False
    with map ready) before triggering. Also resets counter on any map_id
    change to handle Bjora->Longeyes brief loading-gap windows that bypassed
    the 2-poll threshold.
    """
    ConsoleLog("DisconnectGuard", "Started monitoring connection status.", Py4GW.Console.MessageType.Info)
    consecutive_failures = 0
    last_map_id = -1
    while True:
        if not bot.config.fsm_running:
            consecutive_failures = 0
            yield from Routines.Yield.wait(2000)
            continue

        # Reset counter on any map change — covers transient gaps between
        # IsMapLoading=False and Player agent fully populated.
        try:
            current_map_id = Map.GetMapID()
        except Exception:
            current_map_id = last_map_id
        if current_map_id != last_map_id:
            consecutive_failures = 0
            last_map_id = current_map_id
            yield from Routines.Yield.wait(5000)
            continue

        # Skip detection during legitimate map transitions
        if Map.IsMapLoading() or not Map.IsMapReady():
            consecutive_failures = 0
            yield from Routines.Yield.wait(5000)
            continue

        if not Player.IsPlayerLoaded():
            consecutive_failures += 1
            if consecutive_failures >= 3:
                ConsoleLog("DisconnectGuard", "DISCONNECTED detected! Resigning.", Py4GW.Console.MessageType.Error)
                Player.SendChatCommand("resign")
                consecutive_failures = 0
                yield from Routines.Yield.wait(5000)
                continue
        else:
            consecutive_failures = 0

        yield from Routines.Yield.wait(5000)


def UseHoneycombIfNeeded(bot: Botting):
    """Consume a honeycomb if the player does not have the +10% morale buff (morale >= 110)."""
    current_morale = Player.GetMorale()
    ConsoleLog("Morale Check", f"Current morale: {current_morale}", Py4GW.Console.MessageType.Debug)
    
    if current_morale >= 110:
        ConsoleLog("Morale Check", "Already at +10% morale boost, skipping honeycomb.", Py4GW.Console.MessageType.Debug)
        yield
        return
    
    honeycomb_item_id = GLOBAL_CACHE.Inventory.GetFirstModelID(ModelID.Honeycomb.value)
    if honeycomb_item_id:
        ConsoleLog("Morale Check", f"Using honeycomb (item_id={honeycomb_item_id}) to reach +10% morale.", Py4GW.Console.MessageType.Info)
        GLOBAL_CACHE.Inventory.UseItem(honeycomb_item_id)
        # Wait up to 6 seconds for the morale buff to apply (server lag can push past 3s)
        start = Utils.GetBaseTimestamp()
        while Player.GetMorale() < 110:
            if Utils.GetBaseTimestamp() - start > 6000:
                ConsoleLog("Morale Check", "Timeout waiting for honeycomb buff.", Py4GW.Console.MessageType.Warning)
                break
            yield from Routines.Yield.wait(250)
    else:
        ConsoleLog("Morale Check", "No honeycomb in inventory! Cannot get +10% morale boost.", Py4GW.Console.MessageType.Warning)
        yield


def TownRoutines(bot: Botting) -> None:
    bot.States.AddHeader("Town Routines")
    bot.Map.Travel(target_map_id=650) #target_map_name="Longeyes Ledge")
    InitializeBot(bot)
    bot.States.AddCustomState(lambda: EquipSkillBar(bot), "Equip SkillBar")
    HandleInventory(bot)
    bot.States.AddHeader("Exit to Bjora Marches")
    bot.Party.SetHardMode(True) #set hard mode on
    bot.Move.XYAndExitMap(-26375, 16180, target_map_id=482) # target_map_name="Bjora Marches")
    
def TraverseBjoraMarches(bot: Botting) -> None:
    bot.States.AddHeader("Traverse Bjora Marches")
    bot.Player.SetTitle(TitleID.Norn.value)
    path_points_to_traverse_bjora_marches: List[Tuple[float, float]] = [
    (17810, -17649),(17516, -17270),(17166, -16813),(16862, -16324),(16472, -15934),
    (15929, -15731),(15387, -15521),(14849, -15312),(14311, -15101),(13776, -14882),
    (13249, -14642),(12729, -14386),(12235, -14086),(11748, -13776),(11274, -13450),
    (10839, -13065),(10572, -12590),(10412, -12036),(10238, -11485),(10125, -10918),
    (10029, -10348),(9909, -9778)  ,(9599, -9327)  ,(9121, -9009)  ,(8674, -8645)  ,
    (8215, -8289)  ,(7755, -7945)  ,(7339, -7542)  ,(6962, -7103)  ,(6587, -6666)  ,
    (6210, -6226)  ,(5834, -5788)  ,(5457, -5349)  ,(5081, -4911)  ,(4703, -4470)  ,
    (4379, -3990)  ,(4063, -3507)  ,(3773, -3031)  ,(3452, -2540)  ,(3117, -2070)  ,
    (2678, -1703)  ,(2115, -1593)  ,(1541, -1614)  ,(960, -1563)   ,(388, -1491)   ,
    (-187, -1419)  ,(-770, -1426)  ,(-1343, -1440) ,(-1922, -1455) ,(-2496, -1472) ,
    (-3073, -1535) ,(-3650, -1607) ,(-4214, -1712) ,(-4784, -1759) ,(-5278, -1492) ,
    (-5754, -1164) ,(-6200, -796)  ,(-6632, -419)  ,(-7192, -300)  ,(-7770, -306)  ,
    (-8352, -286)  ,(-8932, -258)  ,(-9504, -226)  ,(-10086, -201) ,(-10665, -215) ,
    (-11247, -242) ,(-11826, -262) ,(-12400, -247) ,(-12979, -216) ,(-13529, -53)  ,
    (-13944, 341)  ,(-14358, 743)  ,(-14727, 1181) ,(-15109, 1620) ,(-15539, 2010) ,
    (-15963, 2380) ,(-18048, 4223 ), (-19196, 4986),(-20000, 5595) ,(-20300, 5600)
    ]
    bot.Move.FollowPathAndExitMap(path_points_to_traverse_bjora_marches, target_map_id=546) #target_map_name="Jaga Moraine")
    
def printEach(bot: Botting, seconds: int):
    while True:
        ConsoleLog("Each", f"Each {seconds} seconds", Py4GW.Console.MessageType.Info)
        yield from Routines.Yield.wait(seconds * 1000)
   
    
def PeriodicAutoSalvage(bot: Botting):
    """Coroutine that runs auto-id and auto-salvage every 20 seconds.
    
    This prevents disconnects caused by overwhelming the client with
    too many rapid inventory actions immediately after looting.
    """
    _SALVAGE_INTERVAL_MS = 20 * 1000  # 20 seconds
    
    while True:
        if not bot.config.fsm_running:
            yield from Routines.Yield.wait(2000)
            continue
            
        if Map.IsOutpost():
            yield from Routines.Yield.wait(5000)
            continue
            
        if not Player.IsPlayerLoaded():
            yield from Routines.Yield.wait(5000)
            continue
            
        ConsoleLog("AutoSalvage", "Running periodic auto-id and salvage...", Py4GW.Console.MessageType.Debug)
        bot.Items.AutoIDAndSalvageItems()
        yield from Routines.Yield.wait(_SALVAGE_INTERVAL_MS)


def JagaMoraineFarmRoutine(bot: Botting) -> None:
    def _follow_and_wait(path_points: List[Tuple[float, float]], wait_state_name: str, cycle_timeout: int = 150):
        bot.Move.FollowPath(path_points)
        bot.States.AddCustomState(lambda: WaitForBall(bot, wait_state_name, cycle_timeout), f"Wait for {wait_state_name}")


    bot.States.AddHeader("Jaga Moraine Farm Routine")
    InitializeBot(bot)
    bot.States.AddManagedCoroutine("DisconnectGuard", lambda: DisconnectGuard(bot))
    bot.States.AddManagedCoroutine("PeriodicAutoSalvage", lambda: PeriodicAutoSalvage(bot))
    bot.States.AddCustomState(lambda: UseHoneycombIfNeeded(bot), "Use Honeycomb for Morale")
    bot.States.AddCustomState(lambda: AssignBuild(bot), "Assign Build")
    bot.States.AddCustomState(lambda: TrackStartRun(bot), "Track: Run Start")
    bot.Move.XY(13372.44, -20758.50)
    bot.Dialogs.AtXY(13367, -20771,0x84)
    bot.States.AddManagedCoroutine("HandleStuckJagaMoraine", lambda: HandleStuckJagaMoraine(bot))
    path: List[Tuple[float, float]] = [(13367, -20771),
    (11375, -22761), (10925, -23466), (10917, -24311), (10280, -24620),
    (10280, -24620),(9640, -23175), (7815, -23200), (6626.51, -23167.24)]
    _follow_and_wait(path, "Inner Packs", cycle_timeout=75)
    
    path: List[Tuple[float, float]] = [(7765, -22940), (8213, -22829), (8740, -22475), (8880, -21384),
    (8684, -20833), (8982, -20576),]
    bot.States.AddHeader("Wait for Left Aggro Ball")
    _follow_and_wait(path, "Left Aggro Ball")

    path: List[Tuple[float, float]] = [(10196, -20124), (10123, -19529),(10049, -18933), ]
    _follow_and_wait(path, "log side packs", cycle_timeout=75)
    path: List[Tuple[float, float]] = [
    (9976, -18338), (11316, -18056),
    (10392, -17512), (10114, -16948),]
    _follow_and_wait(path, "Big Pack")
    
    path =[
    (10729, -16273), (10505, -14750),(10815, -14790),
    (11090, -15345), (11670, -15457),(12604, -15320), (12450, -14800),(12725, -14850),
    (12476, -16157),]
    _follow_and_wait(path, "Right Aggro Ball")
    
    bot.Properties.Set("movement_tolerance",value=25)
    path_points_to_killing_spot: List[Tuple[float, float]] = [
        (13070, -16911), (12938, -17081), (12790, -17201), (12747, -17220), (12703, -17239),
        (12684, -17184), (12485.18, -17260.41)]
    bot.Move.FollowPath(path_points_to_killing_spot)
    bot.Properties.ResetTodefault("movement_tolerance", field= "value")
    bot.States.AddHeader("Kill Enemies")
    bot.States.AddCustomState(lambda: KillEnemies(bot), "Kill Enemies")
    bot.Properties.Disable("auto_combat")
    bot.States.RemoveManagedCoroutine("HandleStuckJagaMoraine")
    bot.States.AddHeader("Loot Items")
    bot.Items.LootItems()
    bot.States.AddCustomState(lambda: NeedsInventoryManagement(bot), "Needs Inventory Management")
    bot.States.AddCustomState(lambda: TrackEndRunSuccess(bot), "Track: Run End (success)")
    bot.States.RemoveManagedCoroutine("PeriodicAutoSalvage")
    bot.Properties.Disable("birthday_cupcake")
    bot.Move.XYAndExitMap(15850,-20550, target_map_id=482) # target_map_name="Bjora Marches")
    
    
def NeedsInventoryManagement(bot: Botting):
    # Guard against disconnects
    if not Player.IsPlayerLoaded():
        ConsoleLog("Inventory Check", "Player disconnected! Aborting inventory check.", Py4GW.Console.MessageType.Error)
        yield from Routines.Yield.wait(2000)
        return
        
    free_slots_in_inventory = GLOBAL_CACHE.Inventory.GetFreeSlotCount()
    leave_empty_slots = bot.Properties.Get("leave_empty_inventory_slots", "value")
    if leave_empty_slots is None:
        leave_empty_slots = 10
    else:
        try:
            leave_empty_slots = int(leave_empty_slots)
        except (ValueError, TypeError):
            leave_empty_slots = 10

    count_of_id_kits = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Superior_Identification_Kit.value)
    count_of_salvage_kits = GLOBAL_CACHE.Inventory.GetModelCount(ModelID.Salvage_Kit.value)

    ConsoleLog("Inventory Check",
               f"Free slots: {free_slots_in_inventory} (need >= {leave_empty_slots}), "
               f"ID kits: {count_of_id_kits}, Salvage kits: {count_of_salvage_kits}",
               Py4GW.Console.MessageType.Debug)

    if (
        free_slots_in_inventory < leave_empty_slots
        or count_of_id_kits == 0
        or count_of_salvage_kits == 0
    ):
        ConsoleLog("Inventory Check", "Inventory full or kits missing - resigning!", Py4GW.Console.MessageType.Notice)
        if _yavb_tracker._run_active:
            _yavb_tracker.end_run(success=False)
        Player.SendChatCommand("resign")
        yield from Routines.Yield.wait(1000)
    yield
    
    
def SmartResetDecision(bot: Botting):
    """Conditional: route through Town Routines only when inventory is getting
    full. Otherwise skip Town and go direct to next Jaga run (faster cycle).
    Threshold default 15 free slots — well above the resign threshold of 10."""
    free_slots = GLOBAL_CACHE.Inventory.GetFreeSlotCount()
    threshold = 15
    if free_slots < threshold:
        ConsoleLog("SmartReset", f"Free slots {free_slots} < {threshold} — routing through Town for deposit/restock.", Py4GW.Console.MessageType.Info)
        bot.States.JumpToStepName("[H]Town Routines_1")
    else:
        ConsoleLog("SmartReset", f"Free slots {free_slots} >= {threshold} — skip Town, direct next run.", Py4GW.Console.MessageType.Info)
    yield


def ResetFarmLoop(bot: Botting) -> None:
    # Smart reset: only route through Town Routines (deposit + restock) when
    # inventory is actually getting full. Otherwise direct back to Jaga.
    # Eliminates inv-full death loop AND avoids unnecessary Longeyes detours
    # when slots are still plenty.
    bot.States.AddHeader("Reset Farm Loop")
    bot.States.AddCustomState(lambda: SmartResetDecision(bot), "Smart Reset Decision")
    # Fall-through path (only runs if SmartResetDecision did NOT JumpToStepName):
    bot.Move.XYAndExitMap(-20300, 5600, target_map_id=546)  # Bjora -> Jaga
    bot.States.JumpToStepName("[H]Jaga Moraine Farm Routine_6")
    
def KillEnemies(bot: Botting):
    global in_killing_routine
    in_killing_routine = True
    build = bot.config.build_handler
    if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
        build.SetKillingRoutine(in_killing_routine)
        
    player_pos = Player.GetXY()
    enemy_array = Routines.Agents.GetFilteredEnemyArray(player_pos[0],player_pos[1],Range.Spellcast.value)
    
    start_time = Utils.GetBaseTimestamp()
    timeout = 120000
    
    while len(enemy_array) > 0: #sometimes not all enemies are killed
        current_time = Utils.GetBaseTimestamp()
        delta = current_time - start_time
        if delta > timeout and timeout > 0:
            ConsoleLog("Killing Routine", "Timeout reached, restarting.", Py4GW.Console.MessageType.Error)
            Player.SendChatCommand("resign") 
            yield from Routines.Yield.wait(500)
            return
  
        if Agent.IsDead(Player.GetAgentID()):
            ConsoleLog("Killing Routine", "Player is dead, restarting.", Py4GW.Console.MessageType.Warning)
            yield from Routines.Yield.wait(500)
            return 
        yield from Routines.Yield.wait(1000)
        enemy_array = Routines.Agents.GetFilteredEnemyArray(player_pos[0],player_pos[1],Range.Spellcast.value)
    
    in_killing_routine = False
    finished_routine = True
    if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
        build.SetKillingRoutine(False)
        build.SetRoutineFinished(finished_routine)
    ConsoleLog("Killing Routine", "Finished Killing Routine", Py4GW.Console.MessageType.Info)
    yield from Routines.Yield.wait(1000)  # Wait a bit to ensure the enemies are dead
    


def AssignBuild(bot: Botting):
    profession, _ = Agent.GetProfessionNames(Player.GetAgentID())
    match profession:
        case "Assassin":
            bot.OverrideBuild(SF_Ass_vaettir())
        case "Mesmer":
            bot.OverrideBuild(SF_Mes_vaettir())  # Placeholder for Mesmer build 
        case _:
            ConsoleLog("Unsupported Profession", f"The profession '{profession}' is not supported by this bot.", Py4GW.Console.MessageType.Error, True)
            yield from Routines.Yield.wait(500)
            return
    yield
    
def EquipSkillBar(bot: Botting):
    yield from AssignBuild(bot)
    yield from bot.config.build_handler.LoadSkillBar()


def _set_build_stuck_signal(build: BuildMgr, stuck_counter: int) -> None:
    if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
        build.SetStuckSignal(stuck_counter)


def HandleInventory(bot: Botting) -> None:
    bot.States.AddHeader("Inventory Handling")
    # Slow down inventory management to prevent disconnects from action spam
    bot.Items.AutoIDAndSalvageAndDepositItems()
    bot.Wait.ForTime(1000)
    bot.Move.XYAndInteractNPC(-23110, 14942) # Merchant in Longeyes Ledge
    bot.Wait.ForTime(1000)
    bot.Merchant.SellMaterialsToMerchant()
    bot.Wait.ForTime(500)
    bot.States.AddCustomState(lambda: SellWoodPlanksFromStorage(bot), "Sell Wood Planks from Storage")
    bot.Merchant.Restock.IdentifyKits()
    bot.Wait.ForTime(250)
    bot.Merchant.Restock.SalvageKits()
    bot.Wait.ForTime(250)
    bot.Items.Restock.Honeycomb()
    bot.Wait.ForTime(250)
    bot.Items.AutoIDAndSalvageAndDepositItems()
    bot.Wait.ForTime(1000)
    bot.Merchant.SellMaterialsToMerchant()
    bot.Wait.ForTime(500)
    bot.States.AddCustomState(lambda: SellWoodPlanksFromStorage(bot), "Sell Wood Planks from Storage 2")
    bot.Merchant.Restock.IdentifyKits()
    bot.Wait.ForTime(250)
    bot.Merchant.Restock.SalvageKits()
    bot.Wait.ForTime(250)
    bot.Items.Restock.BirthdayCupcake()
    
def SellWoodPlanksFromStorage(bot: Botting):
    """If storage is nearly full, withdraw and sell all Wood Planks from storage."""
    total_items, bag_size = GLOBAL_CACHE.Inventory.GetStorageSpace()
    free_slots = bag_size - total_items
    
    ConsoleLog("Storage Check", f"Storage space: {total_items}/{bag_size} ({free_slots} free)", Py4GW.Console.MessageType.Debug)
    
    if free_slots > 10:
        yield
        return
    
    ConsoleLog("Storage Check", "Storage nearly full! Selling Wood Planks from inventory and storage.", Py4GW.Console.MessageType.Notice)
    
    # Step 1: Sell any Wood Planks still in inventory
    wood_plank_ids = GLOBAL_CACHE.Inventory.GetAllItemIdsByModelID(ModelID.Wood_Plank.value)
    if wood_plank_ids:
        ConsoleLog("Storage Check", f"Selling {len(wood_plank_ids)} Wood Plank stack(s) from inventory.", Py4GW.Console.MessageType.Info)
        for item_id in wood_plank_ids:
            value = GLOBAL_CACHE.Item.Properties.GetValue(item_id)
            if value > 0:
                GLOBAL_CACHE.Trading.Merchant.SellItem(item_id, value)
                yield from Routines.Yield.wait(75)
    
    # Step 2: Withdraw and sell from storage in batches
    max_iterations = 20  # Safety limit
    for _ in range(max_iterations):
        storage_count = GLOBAL_CACHE.Inventory.GetModelCountInStorage(ModelID.Wood_Plank.value)
        if storage_count <= 0:
            break
        
        # Make sure we have free inventory space
        inv_free = GLOBAL_CACHE.Inventory.GetFreeSlotCount()
        if inv_free <= 0:
            ConsoleLog("Storage Check", "Inventory full, cannot withdraw more Wood Planks.", Py4GW.Console.MessageType.Warning)
            break
        
        # Withdraw (one stack at a time to be safe)
        to_withdraw = min(storage_count, 250)
        ConsoleLog("Storage Check", f"Withdrawing up to {to_withdraw} Wood Planks from storage.", Py4GW.Console.MessageType.Info)
        GLOBAL_CACHE.Inventory.WithdrawItemFromStorageByModelID(ModelID.Wood_Plank.value, to_withdraw)
        yield from Routines.Yield.wait(750)
        
        # Sell what we just withdrew
        wood_plank_ids = GLOBAL_CACHE.Inventory.GetAllItemIdsByModelID(ModelID.Wood_Plank.value)
        if wood_plank_ids:
            ConsoleLog("Storage Check", f"Selling {len(wood_plank_ids)} Wood Plank stack(s) from storage.", Py4GW.Console.MessageType.Info)
            for item_id in wood_plank_ids:
                value = GLOBAL_CACHE.Item.Properties.GetValue(item_id)
                if value > 0:
                    GLOBAL_CACHE.Trading.Merchant.SellItem(item_id, value)
                    yield from Routines.Yield.wait(75)
    
    ConsoleLog("Storage Check", "Done selling Wood Planks.", Py4GW.Console.MessageType.Info)
    yield

def _wait_for_aggro_ball(bot: Botting, side_label: str, cycle_timeout: int = 150):
    from Py4GWCoreLib.Agent import Agent
    """
    Shared logic for waiting until enemies have balled up.
    side_label is just used for logging ("Left" / "Right").
    """
    global in_waiting_routine
    ConsoleLog(f"Waiting for {side_label} Aggro Ball",
               "Waiting for enemies to ball up.",
               Py4GW.Console.MessageType.Info)

    in_waiting_routine = True
    elapsed = 0
    build = bot.config.build_handler

    try:
        while elapsed < cycle_timeout:  # 150 * 100ms = 15s max
            yield from Routines.Yield.wait(100)
            elapsed += 1

            # hard exit if player dies
            if Agent.IsDead(Player.GetAgentID()):
                ConsoleLog(f"{side_label} Aggro Ball Wait",
                           "Player is dead, exiting wait.",
                           Py4GW.Console.MessageType.Warning)
                yield
                return

            # Get player position
            px, py = Player.GetXY()

            # Get enemies within earshot
            enemies_ids = Routines.Agents.GetFilteredEnemyArray(px, py, Range.Earshot.value)

            # Check if all enemies are within Adjacent range
            all_in_adjacent = True
            for enemy_id in enemies_ids:
                
                enemy = Agent.GetAgentByID(enemy_id)
                if enemy is None:
                    continue
                dx, dy = enemy.pos.x - px, enemy.pos.y - py
                if dx * dx + dy * dy > (Range.Adjacent.value ** 2):
                    all_in_adjacent = False
                    break

            if all_in_adjacent:
                ConsoleLog(f"{side_label} Aggro Ball Wait",
                           "Enemies balled up successfully.",
                           Py4GW.Console.MessageType.Info)
                break  # exit early

        else:
            # ← executes only if loop ran full timeout
            ConsoleLog(f"{side_label} Aggro Ball Wait",
                       f"Timeout reached {cycle_timeout*100}ms, exiting without ball.",
                       Py4GW.Console.MessageType.Warning)

    finally:
        # Always reset, no matter why we exited
        in_waiting_routine = False

        # Resume build if applicable
        if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
            yield from build.CastHeartOfShadow()


def WaitForBall(bot: Botting, side_label: str, cycle_timeout: int = 150):
    yield from _wait_for_aggro_ball(bot, side_label, cycle_timeout)

#region Events
    
def _on_death(bot: "Botting"):
    yield from Routines.Yield.wait(10000)
    fsm = bot.config.FSM
    fsm.jump_to_state_by_name("[H]Town Routines_1") 
    fsm.resume()                           
    yield  
    
def on_death(bot: "Botting"):
    ConsoleLog("Death detected", "Player Died - Run Failed, Restarting...", Py4GW.Console.MessageType.Notice)
    if _yavb_tracker._run_active:
        _yavb_tracker.end_run(success=False)
    ActionQueueManager().ResetAllQueues()
    fsm = bot.config.FSM
    fsm.pause()
    fsm.AddManagedCoroutine("OnDeath", _on_death(bot))
    
#region Stuck
in_waiting_routine = False
finished_routine = False
stuck_counter = 0
stuck_timer = ThrottledTimer(5000)
stuck_timer.Start()
try:
    BJORA_MARCHES = Map.GetMapIDByName("Bjora Marches")
except Exception:
    BJORA_MARCHES = 0
try:
    JAGA_MORAINE = Map.GetMapIDByName("Jaga Moraine")
except Exception:
    JAGA_MORAINE = 0
movement_check_timer = ThrottledTimer(3000)
old_player_position = (0,0)
in_killing_routine = False

        
def HandleStuckJagaMoraine(bot: Botting):
    global in_waiting_routine, finished_routine, stuck_counter
    global stuck_timer, movement_check_timer, JAGA_MORAINE
    global old_player_position, in_killing_routine
    
    log_actions = False
    forced_log = True

    ConsoleLog("Stuck Detection", "Starting Stuck Detection Coroutine.", Py4GW.Console.MessageType.Info, forced_log)

    while True:
        if not Routines.Checks.Map.MapValid():
            ConsoleLog("HandleStuck", "Map is not valid, halting...", Py4GW.Console.MessageType.Debug, forced_log)
            yield from Routines.Yield.wait(1000)
            return

        if Agent.IsDead(Player.GetAgentID()):
            ConsoleLog("HandleStuck", "Player is dead, exiting stuck handler.", Py4GW.Console.MessageType.Debug, forced_log)
            yield from Routines.Yield.wait(1000)
            return


        build: BuildMgr = bot.config.build_handler
        
        instance_time = Map.GetInstanceUptime() / 1000  # Convert ms to seconds
        if instance_time > 7 * 60:  # 7 minutes in seconds
            ConsoleLog("HandleStuck", "Instance time exceeded 7 minutes, force resigning.", Py4GW.Console.MessageType.Debug, forced_log)
            stuck_counter = 0
            if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
                _set_build_stuck_signal(build, stuck_counter)
                
            Player.SendChatCommand("resign") 
            yield from Routines.Yield.wait(500)
            return

        # Waiting routine check
        if in_waiting_routine:
            ConsoleLog("HandleStuck", "In waiting routine, resetting stuck counter.", Py4GW.Console.MessageType.Debug, log_actions)
            stuck_counter = 0
            if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
                _set_build_stuck_signal(build, stuck_counter)
            stuck_timer.Reset()
            yield from Routines.Yield.wait(1000)
            continue

        # Finished routine check
        if finished_routine:
            ConsoleLog("HandleStuck", "Finished routine, resetting stuck counter.", Py4GW.Console.MessageType.Debug, log_actions)
            stuck_counter = 0
            if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
                _set_build_stuck_signal(build, stuck_counter)
            stuck_timer.Reset()
            yield from Routines.Yield.wait(1000)
            continue

        # Killing routine check
        if in_killing_routine:
            ConsoleLog("HandleStuck", "In killing routine, resetting stuck counter.", Py4GW.Console.MessageType.Debug, log_actions)
            stuck_counter = 0
            if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
                _set_build_stuck_signal(build, stuck_counter)
            stuck_timer.Reset()
            yield from Routines.Yield.wait(1000)
            continue

        # Jaga Moraine map check
        global JAGA_MORAINE
        if JAGA_MORAINE == 0:
            try:
                JAGA_MORAINE = Map.GetMapIDByName("Jaga Moraine")
            except Exception:
                pass
        if Map.GetMapID() == JAGA_MORAINE:
            if stuck_timer.IsExpired():
                ConsoleLog("HandleStuck", "Issuing scheduled /stuck command.", Py4GW.Console.MessageType.Debug, log_actions)
                Player.SendChatCommand("stuck")
                stuck_timer.Reset()

            if movement_check_timer.IsExpired():
                current_player_pos = Player.GetXY()
                ConsoleLog("HandleStuck", f"Checking movement. Old pos: {old_player_position}, Current pos: {current_player_pos}", Py4GW.Console.MessageType.Debug, log_actions)

                if old_player_position == current_player_pos:
                    ConsoleLog("HandleStuck", "Player is stuck, sending /stuck command.", Py4GW.Console.MessageType.Warning, forced_log)
                    Player.SendChatCommand("stuck")
                    stuck_counter += 1
                    ConsoleLog("HandleStuck", f"Stuck counter incremented to {stuck_counter}.", Py4GW.Console.MessageType.Debug, log_actions)
                    if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
                        _set_build_stuck_signal(build, stuck_counter)
                    stuck_timer.Reset()
                else:
                    old_player_position = current_player_pos
                    if stuck_counter > 0:
                        ConsoleLog("HandleStuck", "Player moved, resetting stuck counter to 0.", Py4GW.Console.MessageType.Info, log_actions)
                    stuck_counter = 0

                movement_check_timer.Reset()

            if stuck_counter >= 10:
                ConsoleLog("HandleStuck", "Unrecoverable stuck detected, force resigning.", Py4GW.Console.MessageType.Error, forced_log)
                stuck_counter = 0
                if isinstance(build, SF_Ass_vaettir) or isinstance(build, SF_Mes_vaettir):
                    _set_build_stuck_signal(build, stuck_counter)
                
                Player.SendChatCommand("resign") 
                yield from Routines.Yield.wait(500)
                return
        else:
            ConsoleLog("HandleStuck", "Not in Jaga Moraine, halting.", Py4GW.Console.MessageType.Info, forced_log)
            yield from Routines.Yield.wait(1000)
            return

        ConsoleLog("HandleStuck", "waiting for next check.", Py4GW.Console.MessageType.Info, log_actions)
        yield from Routines.Yield.wait(500)
        continue




 
bot.SetMainRoutine(create_bot_routine)
base_path = Py4GW.Console.get_projects_path()


"""def configure():
    global bot
    bot.UI.draw_configure_window()"""
    
def tooltip():
    PyImGui.begin_tooltip()

    # Title
    title_color = Color(255, 200, 100, 255)
    ImGui.push_font("Regular", 20)
    PyImGui.text_colored("Yet Another Vaettir Bot (Y.A.V.B) 2.0", title_color.to_tuple_normalized())
    ImGui.pop_font()
    PyImGui.spacing()
    PyImGui.separator()

    # Description
    PyImGui.text("A bot designed for farming Vaettir in Jaga Moraine.")
    PyImGui.spacing()

    # Features
    PyImGui.text_colored("Features:", title_color.to_tuple_normalized())
    PyImGui.text_colored("Features:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Inventory Control: Automated management with birthday cupcake restock")
    PyImGui.bullet_text("Stuck Recovery: Advanced detection to handle pathing issues in Bjora Marches")
    PyImGui.bullet_text("Smart Combat: Precision timing for Shadow Form and defensive skill rotations")
    PyImGui.bullet_text("Auto-Reset: Automatically loops the farm until inventory or supply limits are hit")
    PyImGui.bullet_text("Supports")
    PyImGui.same_line(0,-1)
    assassin_color = ColorPalette.GetColor("gw_assassin")
    mesmer_color = ColorPalette.GetColor("gw_mesmer")
    
    PyImGui.text_colored("Assassin", assassin_color.to_tuple_normalized())
    PyImGui.same_line(0,-1)
    PyImGui.text(" and ")
    PyImGui.same_line(0,-1)
    PyImGui.text_colored("Mesmer", mesmer_color.to_tuple_normalized())
    PyImGui.same_line(0,-1)
    PyImGui.text(" professions")

    PyImGui.spacing()
    PyImGui.separator()
    PyImGui.spacing()

    # Credits
    PyImGui.text_colored("Credits:", title_color.to_tuple_normalized())
    PyImGui.bullet_text("Developed by Apo")
    PyImGui.bullet_text("Contributors: Mark")

    PyImGui.end_tooltip()

    
        
def main():

    bot.Update()
    projects_path = Py4GW.Console.get_projects_path()
    widgets_path = projects_path + "\\Widgets\\Config\\textures\\"
    bot.UI.draw_window(icon_path=widgets_path + "YAVB 2.0 mascot.png")


if __name__ == "__main__":
    main()
