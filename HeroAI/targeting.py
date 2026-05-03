import time as _time

from Py4GWCoreLib import GLOBAL_CACHE, Utils, AgentArray, Routines, Agent, Player, Party, Map
from Py4GWCoreLib.EnemyBlacklist import EnemyBlacklist
from .constants import (
    MASTER_EMAIL,
    Range,
    BLOOD_IS_POWER,
    BLOOD_RITUAL,
    MAX_NUM_PLAYERS,
)

# Module-level enemy array cache. Keep this short and position-aware so moving
# followers do not keep stale empty enemy scans after entering combat range.
_ENEMY_CACHE_TTL = 0.25
_ENEMY_CACHE_CELL_SIZE = 250.0
_enemy_cache: dict[tuple[int, float, int, int], tuple[float, list]] = {}
_enemy_pos_cache_data: tuple[float, tuple[int, float, int, int] | None, list[tuple[int, float, float]]] = (0.0, None, [])


def _enemy_cache_key(px: float, py: float, area: float) -> tuple[int, float, int, int]:
    return (
        int(Map.GetMapID() or 0),
        float(area),
        int(float(px) // _ENEMY_CACHE_CELL_SIZE),
        int(float(py) // _ENEMY_CACHE_CELL_SIZE),
    )


def _get_cached_enemies(px: float, py: float, area: float) -> list:
    """Return filtered enemy array, cached briefly per range and position cell."""
    now = _time.time()
    key = _enemy_cache_key(px, py, area)
    entry = _enemy_cache.get(key)
    if entry and now - entry[0] < _ENEMY_CACHE_TTL:
        return entry[1]
    result = Routines.Agents.GetFilteredEnemyArray(px, py, area)
    _enemy_cache[key] = (now, result)
    return result


def _get_cached_enemy_positions(px: float, py: float, area: float) -> list[tuple[int, float, float]]:
    """Return [(agent_id, x, y)] for alive enemies, cached briefly."""
    global _enemy_pos_cache_data
    now = _time.time()
    key = _enemy_cache_key(px, py, area)
    if (
        _enemy_pos_cache_data[1] == key
        and now - _enemy_pos_cache_data[0] < _ENEMY_CACHE_TTL
    ):
        return _enemy_pos_cache_data[2]
    enemies = _get_cached_enemies(px, py, area)
    enemies = AgentArray.Filter.ByCondition(
        enemies, lambda aid: Agent.IsValid(aid) and not Agent.IsDead(aid)
    )
    positions = [(eid, *Agent.GetXY(eid)) for eid in enemies]
    _enemy_pos_cache_data = (now, key, positions)
    return positions


def _filter_blacklisted(agent_id: int) -> int:
    """Return 0 if the agent is blacklisted (by model ID or name), otherwise return agent_id unchanged."""
    if agent_id == 0:
        return 0
    bl = EnemyBlacklist()
    if bl.is_empty():
        return agent_id
    return 0 if bl.is_blacklisted(agent_id) else agent_id

def GetAllAlliesArray(distance=Range.SafeCompass.value):
    #Pets are added here
    ally_array = Routines.Targeting.GetAllAlliesArray(distance)
    return ally_array

def FilterAllyArray(array, distance, other_ally=False, filter_skill_id=0):
    #this is multibox!
    from .utils import CheckForEffect
    array = AgentArray.Filter.ByDistance(array, Player.GetXY(), distance)
    array = AgentArray.Filter.ByCondition(array, lambda agent_id: Agent.IsAlive(agent_id))
        
    if other_ally:
        array = AgentArray.Filter.ByCondition(array, lambda agent_id: Player.GetAgentID() != agent_id)
    
    if filter_skill_id != 0:
        array = AgentArray.Filter.ByCondition(array, lambda agent_id: not CheckForEffect(agent_id, filter_skill_id))
    
    return array

_party_order_cache: tuple[float, dict, dict, dict, int] = (0.0, {}, {}, {}, 0)
_PARTY_ORDER_TTL = 3.0


def _get_party_order() -> tuple[dict, dict, dict, int]:
    """Return (player_order, hero_order, pet_owner_order, fallback_index), cached 3s."""
    global _party_order_cache
    now = _time.time()
    if now - _party_order_cache[0] < _PARTY_ORDER_TTL:
        return _party_order_cache[1], _party_order_cache[2], _party_order_cache[3], _party_order_cache[4]

    player_order = {}
    for index, player in enumerate(Party.GetPlayers() or []):
        agent_id = int(Party.Players.GetAgentIDByLoginNumber(player.login_number) or 0)
        if agent_id:
            player_order[agent_id] = index

    hero_order = {}
    hero_start = len(player_order)
    for index, hero in enumerate(Party.GetHeroes() or []):
        agent_id = int(getattr(hero, "agent_id", 0) or 0)
        if agent_id:
            hero_order[agent_id] = hero_start + index

    pet_owner_order = {}
    for owner_agent_id, order in player_order.items():
        pet_id = int(Party.Pets.GetPetID(owner_agent_id) or 0)
        if pet_id:
            pet_owner_order[pet_id] = order

    fallback_index = hero_start + len(hero_order)
    _party_order_cache = (now, player_order, hero_order, pet_owner_order, fallback_index)
    return player_order, hero_order, pet_owner_order, fallback_index


def SortAlliesByPartyPosition(agent_array):
    player_order, hero_order, pet_owner_order, fallback_index = _get_party_order()

    def sort_key(agent_id):
        if agent_id in player_order:
            return (0, player_order[agent_id], agent_id)
        if agent_id in hero_order:
            return (1, hero_order[agent_id], agent_id)
        if agent_id in pet_owner_order:
            return (2, pet_owner_order[agent_id], agent_id)
        return (3, fallback_index, agent_id)

    return sorted(agent_array or [], key=sort_key)

def SortAlliesByLowestHp(agent_array):
    """Sort allies by current HP ascending, with party position as a stable
    tiebreak. Python's sort is stable, so equal-HP entries preserve the order
    from SortAlliesByPartyPosition (players -> heroes -> pet-owners)."""
    position_sorted = SortAlliesByPartyPosition(agent_array)
    return sorted(position_sorted, key=lambda agent_id: Agent.GetHealth(agent_id))


def IsResurrectablePartyMember(agent_id: int) -> bool:
    if not agent_id or not Agent.IsValid(agent_id):
        return False
    return Routines.Party.IsPartyMember(agent_id)


def TargetDeadPartyMember(
    distance=Range.Spellcast.value,
    reserve: bool = False,
    skill_id: int = 0,
    aftercast_delay: int = 250,
):
    if reserve:
        return Routines.Agents.GetResurrectionTarget(
            distance,
            reserve=True,
            skill_id=skill_id,
            aftercast_delay=aftercast_delay,
        )
    return Routines.Agents.GetDeadAlly(distance)

def TargetAllyByPredicate(
    predicate=None,
    other_ally=False,
    filter_skill_id=0,
    include_spirit_pets=False,
    distance=Range.Spellcast.value,
):
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id)

    if include_spirit_pets:
        spirit_pet_array = AgentArray.GetSpiritPetArray()
        spirit_pet_array = FilterAllyArray(spirit_pet_array, distance, other_ally, filter_skill_id)
        spirit_pet_array = AgentArray.Filter.ByCondition(spirit_pet_array, lambda agent_id: not Agent.IsSpawned(agent_id))
        ally_array = AgentArray.Manipulation.Merge(ally_array, spirit_pet_array)

    if predicate is not None:
        ally_array = AgentArray.Filter.ByCondition(ally_array, predicate)

    ally_array = SortAlliesByPartyPosition(ally_array)
    return Utils.GetFirstFromArray(ally_array)

def TargetLowestAlly(other_ally=False,filter_skill_id=0):
    distance = Range.Spellcast.value
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id) 
     
    
    spirit_pet_array = AgentArray.GetSpiritPetArray()
    spirit_pet_array = FilterAllyArray(spirit_pet_array, distance, other_ally, filter_skill_id)
    spirit_pet_array = AgentArray.Filter.ByCondition(spirit_pet_array, lambda agent_id: not Agent.IsSpawned(agent_id)) #filter spirits
    ally_array = AgentArray.Manipulation.Merge(ally_array, spirit_pet_array) #added Pets

    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetMinionOrAllyNonEnchanted(filter_skill_id=0, distance=Range.Spellcast.value):
    minion_array = AgentArray.GetMinionArray()
    minion_array = AgentArray.Filter.ByDistance(minion_array, Player.GetXY(), distance)
    minion_array = AgentArray.Filter.ByCondition(minion_array, lambda agent_id: Agent.IsAlive(agent_id))
    minion_array = AgentArray.Filter.ByCondition(minion_array, lambda agent_id: not Agent.IsEnchanted(agent_id))
    minion_array = SortAlliesByLowestHp(minion_array)
    minion_target = Utils.GetFirstFromArray(minion_array)
    if minion_target:
        return minion_target

    return TargetAllyNonEnchanted(distance=distance)


def TargetMinionNonEnchanted(distance=Range.Spellcast.value):
    minion_array = AgentArray.GetMinionArray()
    minion_array = AgentArray.Filter.ByDistance(minion_array, Player.GetXY(), distance)
    minion_array = AgentArray.Filter.ByCondition(minion_array, lambda agent_id: Agent.IsAlive(agent_id))
    minion_array = AgentArray.Filter.ByCondition(minion_array, lambda agent_id: not Agent.IsEnchanted(agent_id))
    minion_array = SortAlliesByLowestHp(minion_array)
    return Utils.GetFirstFromArray(minion_array)


def TargetAllyNonEnchanted(distance=Range.Spellcast.value):
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, False, 0)

    spirit_pet_array = AgentArray.GetSpiritPetArray()
    spirit_pet_array = FilterAllyArray(spirit_pet_array, distance, False, 0)
    spirit_pet_array = AgentArray.Filter.ByCondition(spirit_pet_array, lambda agent_id: not Agent.IsSpawned(agent_id))
    ally_array = AgentArray.Manipulation.Merge(ally_array, spirit_pet_array)

    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: not Agent.IsEnchanted(agent_id))
    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetAllyNonWeaponSpelled(distance=Range.Earshot.value):
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, False, 0)

    spirit_pet_array = AgentArray.GetSpiritPetArray()
    spirit_pet_array = FilterAllyArray(spirit_pet_array, distance, False, 0)
    spirit_pet_array = AgentArray.Filter.ByCondition(spirit_pet_array, lambda agent_id: not Agent.IsSpawned(agent_id))
    ally_array = AgentArray.Manipulation.Merge(ally_array, spirit_pet_array)

    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: not Agent.IsWeaponSpelled(agent_id))
    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetLowestAllyEnergy(other_ally=False, filter_skill_id=0, less_energy=1.0):
    global BLOOD_IS_POWER, BLOOD_RITUAL
    from .utils import (CheckForEffect, GetEnergyValues)
    
    
    distance = Range.Spellcast.value
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id)
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: not CheckForEffect(agent_id, BLOOD_IS_POWER))
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: not CheckForEffect(agent_id, BLOOD_RITUAL))
    
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: GetEnergyValues(agent_id) <= less_energy)

    # Prioritize the ally with the lowest current energy, breaking ties by distance to the caster so
    # the closest eligible ally wins when energy values match.
    player_xy = Player.GetXY()
    ally_array = sorted(
        ally_array or [],
        key=lambda agent_id: (
            GetEnergyValues(agent_id),
            Utils.Distance(Agent.GetXY(agent_id), player_xy),
            agent_id,
        ),
    )

    ally = Utils.GetFirstFromArray(ally_array)
    return ally


def TargetLowestAllyCaster(other_ally=False, filter_skill_id=0):
    from Py4GWCoreLib import Routines
    distance = Range.Spellcast.value
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id)
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: Routines.Checks.Agents.IsCaster(agent_id))

    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetLowestAllyMartial(other_ally=False, filter_skill_id=0):
    from Py4GWCoreLib import Routines
    from .utils import HasIllusionaryWeaponry
    distance = Range.Spellcast.value
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id)
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: Routines.Checks.Agents.IsMartial(agent_id))
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: not HasIllusionaryWeaponry(agent_id))

    spirit_pet_array = AgentArray.GetSpiritPetArray()
    spirit_pet_array = FilterAllyArray(spirit_pet_array, distance, other_ally, filter_skill_id)
    spirit_pet_array = AgentArray.Filter.ByCondition(spirit_pet_array, lambda agent_id: not Agent.IsSpawned(agent_id)) #filter spirits
    ally_array = AgentArray.Manipulation.Merge(ally_array, spirit_pet_array) #added Pets

    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetLowestAllyMelee(other_ally=False, filter_skill_id=0):
    from Py4GWCoreLib import Routines
    from .utils import HasIllusionaryWeaponry
    distance = Range.Spellcast.value
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id)
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: Routines.Checks.Agents.IsMelee(agent_id))
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: not HasIllusionaryWeaponry(agent_id))

    spirit_pet_array = AgentArray.GetSpiritPetArray()
    spirit_pet_array = FilterAllyArray(spirit_pet_array, distance, other_ally, filter_skill_id)
    spirit_pet_array = AgentArray.Filter.ByCondition(spirit_pet_array, lambda agent_id: not Agent.IsSpawned(agent_id)) #filter spirits
    ally_array = AgentArray.Manipulation.Merge(ally_array, spirit_pet_array) #added Pets

    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetLowestAllyRanged(other_ally=False, filter_skill_id=0):
    from Py4GWCoreLib import Routines
    distance = Range.Spellcast.value
    ally_array = AgentArray.GetAllyArray()
    ally_array = FilterAllyArray(ally_array, distance, other_ally, filter_skill_id)
    ally_array = AgentArray.Filter.ByCondition(ally_array, lambda agent_id: Routines.Checks.Agents.IsRanged(agent_id))

    ally_array = SortAlliesByLowestHp(ally_array)
    return Utils.GetFirstFromArray(ally_array)


def TargetNearestItem():
    return Routines.Targeting.TargetNearestItem()


def TargetClusteredEnemy(
    area=4500.0,
    *,
    skill_id: int = 0,
    cluster_radius: float | None = None,
):
    if not skill_id:
        return _filter_blacklisted(
            Routines.Targeting.TargetClusteredEnemy(area, cluster_radius=cluster_radius)
        )

    player_x, player_y = Player.GetXY()
    enemy_positions = _get_cached_enemy_positions(player_x, player_y, area)
    if not enemy_positions:
        return 0

    aoe_range = GLOBAL_CACHE.Skill.Data.GetAoERange(skill_id) or Range.Nearby.value
    effective_cluster_radius = float(
        cluster_radius if cluster_radius is not None else Range.Earshot.value
    )
    player_pos = (player_x, player_y)
    cluster_r_sq = effective_cluster_radius * effective_cluster_radius
    aoe_r_sq = aoe_range * aoe_range

    scored: list[tuple[int, int, float, float, int]] = []
    for agent_id, target_x, target_y in enemy_positions:
        # Compute blob and AoE hits via distance math; no re-scan.
        blob_ids = []
        aoe_count = 0
        cx_sum, cy_sum = 0.0, 0.0
        for eid, ex, ey in enemy_positions:
            dx, dy = ex - target_x, ey - target_y
            dist_sq = dx * dx + dy * dy
            if dist_sq <= cluster_r_sq:
                blob_ids.append(eid)
                cx_sum += ex
                cy_sum += ey
            if dist_sq <= aoe_r_sq:
                aoe_count += 1

        if not blob_ids:
            continue

        n = len(blob_ids)
        center_x = cx_sum / n
        center_y = cy_sum / n
        center_distance = Utils.Distance((target_x, target_y), (center_x, center_y))
        player_distance = Utils.Distance(player_pos, (target_x, target_y))

        scored.append(
            (aoe_count, n, center_distance, player_distance, agent_id)
        )

    if not scored:
        return 0

    scored.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    return _filter_blacklisted(scored[0][4])

def GetEnemyAttacking(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyAttacking(max_distance, aggressive_only))

def GetEnemyCasting(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyCasting(max_distance, aggressive_only))

def GetEnemyCastingSpell(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyCastingSpell(max_distance, aggressive_only))

def GetEnemyCastingSpellOrChant(max_distance=4500.0, aggressive_only=False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyCastingSpellOrChant(max_distance, aggressive_only))

def GetEnemyInjured(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyInjured(max_distance, aggressive_only))

def GetEnemyHealthy(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyHealthy(max_distance, aggressive_only))

def GetEnemyConditioned(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyConditioned(max_distance, aggressive_only))

def GetEnemyBleeding(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyBleeding(max_distance, aggressive_only))

def GetEnemyPoisoned(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyPoisoned(max_distance, aggressive_only))
    
def GetEnemyCrippled(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyCrippled(max_distance, aggressive_only))

def GetEnemyHexed(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyHexed(max_distance, aggressive_only))

def GetEnemyDegenHexed(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyDegenHexed(max_distance, aggressive_only))

def GetEnemyEnchanted(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyEnchanted(max_distance, aggressive_only))

def GetEnemyMoving(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyMoving(max_distance, aggressive_only))

def GetEnemyKnockedDown(max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyKnockedDown(max_distance, aggressive_only))

def GetEnemyWithEffect(effect_skill_id, max_distance=4500.0, aggressive_only = False):
    return _filter_blacklisted(Routines.Targeting.GetEnemyWithEffect(effect_skill_id, max_distance, aggressive_only))


def TargetAllyWeaponSpell(
    weapon_spell_skill_id,
    max_distance=Range.Spellcast.value,
    refresh_window_ms=1000,
    allow_overlap_weapon_spell=False,
):
    # Picks the best ally to receive `weapon_spell_skill_id`.
    # Eligible allies have no conflicting weapon spell, or already carry this same
    # weapon spell with <= refresh_window_ms remaining (refresh tier). Scoring
    # prefers allies about to take damage: most enemies within Earshot first,
    # then lowest HP, then closest to the caster.
    if not weapon_spell_skill_id:
        return 0

    ally_array = GetAllAlliesArray(max_distance) or []
    if not ally_array:
        return 0

    def _is_refresh_eligible(agent_id):
        if allow_overlap_weapon_spell:
            return not Routines.Checks.Agents.HasEffect(agent_id, weapon_spell_skill_id, exact_weapon_spell=True)
        if not Agent.IsWeaponSpelled(agent_id):
            return True
        if not Routines.Checks.Agents.HasEffect(agent_id, weapon_spell_skill_id, exact_weapon_spell=True):
            return False
        remaining_ms = GLOBAL_CACHE.Effects.GetEffectTimeRemaining(agent_id, weapon_spell_skill_id)
        return remaining_ms <= refresh_window_ms

    candidates = [
        agent_id for agent_id in ally_array
        if Agent.IsValid(agent_id)
        and Routines.Checks.Agents.IsAlive(agent_id)
        and _is_refresh_eligible(agent_id)
    ]
    if not candidates:
        return 0

    _px, _py = Player.GetXY()
    _cached_epos = _get_cached_enemy_positions(_px, _py, Range.SafeCompass.value)
    _earshot_sq = Range.Earshot.value * Range.Earshot.value

    player_pos = (_px, _py)
    scored = []
    for agent_id in candidates:
        ax, ay = Agent.GetXY(agent_id)
        scored.append((
            -sum(1 for _, ex, ey in _cached_epos if (ex - ax) ** 2 + (ey - ay) ** 2 <= _earshot_sq),
            Agent.GetHealth(agent_id),
            Utils.Distance(player_pos, (ax, ay)),
            agent_id,
        ))
    scored.sort()
    return scored[0][3]


def TargetMeleeOrMartialClusterEnemy(
    skill_id: int,
    *,
    require_attacking: bool = False,
    max_distance: float = Range.Spellcast.value,
) -> int:
    """Pick the densest-cluster enemy for a melee-group AoE skill.

    Candidate pool is (IsMelee OR IsMartial OR IsAttacking); falls back to
    any enemy when no IsMelee/IsMartial is in range. Ranks by cluster size
    then player-distance. require_attacking=True hard-requires IsAttacking.
    """
    player_x, player_y = Player.GetXY()
    enemy_array = [eid for eid, _, _ in _get_cached_enemy_positions(player_x, player_y, max_distance)]
    if not enemy_array:
        return 0

    aoe_range = GLOBAL_CACHE.Skill.Data.GetAoERange(skill_id) or Range.Nearby.value

    melees_present = any(
        Agent.IsMelee(agent_id) or Agent.IsMartial(agent_id)
        for agent_id in enemy_array
    )

    if melees_present:
        candidates = [
            agent_id for agent_id in enemy_array
            if Agent.IsMelee(agent_id)
            or Agent.IsMartial(agent_id)
            or Agent.IsAttacking(agent_id)
        ]
    else:
        candidates = list(enemy_array)

    if require_attacking:
        candidates = [agent_id for agent_id in candidates if Agent.IsInCombatStance(agent_id)]

    if not candidates:
        return 0

    player_pos = (player_x, player_y)
    aoe_r_sq = aoe_range * aoe_range
    # Pre-cache all enemy positions; one scan, no nested GetFilteredEnemyArray.
    all_positions = [(eid, *Agent.GetXY(eid)) for eid in enemy_array]
    scored: list[tuple[int, float, int]] = []
    for agent_id in candidates:
        target_x, target_y = Agent.GetXY(agent_id)
        cluster_score = sum(
            1 for _, ex, ey in all_positions
            if (ex - target_x) ** 2 + (ey - target_y) ** 2 <= aoe_r_sq
        ) - 1
        distance = Utils.Distance(player_pos, (target_x, target_y))
        scored.append((max(0, cluster_score), distance, agent_id))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return _filter_blacklisted(scored[0][2])


def _target_enemy_clustered(
    max_distance: float,
    radius: float,
) -> int:
    """Internal: enemy with the most neighbours within *radius* (squared-distance, pre-cached positions)."""
    player_x, player_y = Player.GetXY()
    cached_pos = _get_cached_enemy_positions(player_x, player_y, max_distance)
    n = len(cached_pos)
    if n == 0:
        return 0

    positions = [(aid, (ex, ey)) for aid, ex, ey in cached_pos]
    radius_sq = radius * radius

    best_id, best_count = 0, -1
    for i in range(n):
        agent_id, (x1, y1) = positions[i]
        count = 0
        for j in range(n):
            if i == j:
                continue
            _, (x2, y2) = positions[j]
            dx = x2 - x1
            dy = y2 - y1
            if dx * dx + dy * dy <= radius_sq:
                count += 1
        if count > best_count:
            best_count, best_id = count, agent_id

    return _filter_blacklisted(best_id)


def TargetEnemyClusteredNearby(
    max_distance: float = Range.Spellcast.value,
) -> int:
    """Returns the enemy with the most other enemies within Nearby radius (252 units, Energy Surge range)."""
    return _target_enemy_clustered(max_distance, Range.Nearby.value)


def TargetEnemyClusteredAdjacent(
    max_distance: float = Range.Spellcast.value,
) -> int:
    """Returns the enemy with the most other enemies within Adjacent radius (166 units, melee/scythe range)."""
    return _target_enemy_clustered(max_distance, Range.Adjacent.value)


def TargetCasterClusterEnemy(
    skill_id: int,
    *,
    max_distance: float = Range.Spellcast.value,
) -> int:
    """Pick the densest-caster-cluster enemy for a caster-targeted AoE hex.

    Candidate pool is IsCaster only. Ranks by caster-cluster size DESC
    (adjacent casters within the skill's AoE range), then player-distance
    ASC. Returns 0 if no caster is in range.
    """
    player_x, player_y = Player.GetXY()
    enemy_array = [eid for eid, _, _ in _get_cached_enemy_positions(player_x, player_y, max_distance)]
    if not enemy_array:
        return 0

    casters = [agent_id for agent_id in enemy_array if Agent.IsCaster(agent_id)]
    if not casters:
        return 0

    aoe_range = GLOBAL_CACHE.Skill.Data.GetAoERange(skill_id) or Range.Nearby.value
    aoe_r_sq = aoe_range * aoe_range

    # Pre-cache caster positions; one scan, no nested GetFilteredEnemyArray.
    caster_positions = [(cid, *Agent.GetXY(cid)) for cid in casters]
    player_pos = (player_x, player_y)
    scored: list[tuple[int, float, int]] = []
    for agent_id, target_x, target_y in caster_positions:
        cluster_score = sum(
            1 for _, cx, cy in caster_positions
            if (cx - target_x) ** 2 + (cy - target_y) ** 2 <= aoe_r_sq
        ) - 1
        distance = Utils.Distance(player_pos, (target_x, target_y))
        scored.append((max(0, cluster_score), distance, agent_id))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return _filter_blacklisted(scored[0][2])
