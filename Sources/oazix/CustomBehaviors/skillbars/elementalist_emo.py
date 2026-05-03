from typing import List, Any, Generator, Callable, override
import time
from Sources.oazix.CustomBehaviors.primitives.behavior_state import BehaviorState
from Sources.oazix.CustomBehaviors.primitives.scores.score_per_agent_quantity_definition import ScorePerAgentQuantityDefinition
from Sources.oazix.CustomBehaviors.primitives.scores.score_per_health_gravity_definition import ScorePerHealthGravityDefinition
from Sources.oazix.CustomBehaviors.primitives.scores.score_static_definition import ScoreStaticDefinition
from Sources.oazix.CustomBehaviors.primitives.skillbars.custom_behavior_base_utility import CustomBehaviorBaseUtility
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill import CustomSkill
from Sources.oazix.CustomBehaviors.primitives.skills.custom_skill_utility_base import CustomSkillUtilityBase
from Sources.oazix.CustomBehaviors.skills.common.auto_attack_utility import AutoAttackUtility
from Sources.oazix.CustomBehaviors.skills.common.ebon_battle_standard_of_wisdom_utility import EbonBattleStandardOfWisdom
from Sources.oazix.CustomBehaviors.skills.common.ebon_vanguard_assassin_support_utility import EbonVanguardAssassinSupportUtility
from Sources.oazix.CustomBehaviors.skills.common.great_dwarf_weapon_utility import GreatDwarfWeaponUtility
from Sources.oazix.CustomBehaviors.skills.common.i_am_unstoppable_utility import IAmUnstoppableUtility
from Sources.oazix.CustomBehaviors.skills.elementalist.emo_spam_on_party_if_mana_low_utility import EmoSpamOnPartyIfManaLowUtility
from Sources.oazix.CustomBehaviors.skills.generic.dismiss_buff_if_no_mana_utility import DismissBuffIfNoManaUtility
from Sources.oazix.CustomBehaviors.skills.generic.keep_self_effect_up_utility import KeepSelfEffectUpUtility
from Sources.oazix.CustomBehaviors.skills.generic.maintain_effect_up_on_player_utility import MaintainEffectUpOnPlayerUtility
from Sources.oazix.CustomBehaviors.skills.generic.protective_shout_utility import ProtectiveShoutUtility
from Sources.oazix.CustomBehaviors.skills.monk.infuse_health_utility import InfuseHealthUtility
from Sources.oazix.CustomBehaviors.skills.monk.life_attunement_utility import LifeAttunementUtility
from Sources.oazix.CustomBehaviors.skills.monk.life_bond_utility import LifeBondUtility
from Sources.oazix.CustomBehaviors.skills.monk.protective_bond_utility import ProtectiveBondUtility
from Sources.oazix.CustomBehaviors.skills.monk.protective_spirit_utility import ProtectiveSpiritUtility
from Sources.oazix.CustomBehaviors.skills.monk.reversal_of_fortune_utility import ReversalOfFortuneUtility
from Sources.oazix.CustomBehaviors.skills.monk.seed_of_life_utility import SeedOfLifeUtility
from Sources.oazix.CustomBehaviors.skills.monk.spirit_bond_utility import SpiritBondUtility
from Sources.oazix.CustomBehaviors.skills.paragon.fall_back_utility import FallBackUtility
from Sources.oazix.CustomBehaviors.skills.paragon.heroic_refrain_utility import HeroicRefrainUtility

class ElementalistEmo_UtilitySkillBar(CustomBehaviorBaseUtility):

    # Energy thresholds for the E/Mo energy management system
    _ENERGY_FLOOR_FOR_BONDS: int = 30  # Don't cast new bonds below this absolute energy
    _DISMISS_THRESHOLD: float = 0.35   # Drop bonds when energy falls below 35%
    _SPAM_THRESHOLD_DEFAULT: float = 0.70  # Start spamming enchantments to regen below 70%

    def __init__(self):
        super().__init__()
        in_game_build = list(self.skillbar_management.get_in_game_build().values())

        # ORDERED BY PRIORITY

        # -- CORE TOP --
        # Ether Renewal is the energy engine — MUST stay up at all costs
        self.ether_renewal_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(event_bus=self.event_bus, skill=CustomSkill("Ether_Renewal"), current_build=in_game_build, score_definition=ScoreStaticDefinition(90), allowed_states=[BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO, BehaviorState.FAR_FROM_AGGRO])
        self.aura_of_restoration_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(event_bus=self.event_bus, skill=CustomSkill("Aura_of_Restoration"), current_build=in_game_build, score_definition=ScoreStaticDefinition(89), allowed_states=[BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO, BehaviorState.FAR_FROM_AGGRO])

        # -- ENERGY RECOVERY (high priority when mana is low) --
        # Score 85: when energy is low, spam enchantments on party to trigger ER regen
        self.spam_if_mana_low_utility: CustomSkillUtilityBase = EmoSpamOnPartyIfManaLowUtility(event_bus=self.event_bus,
                    skills=[],  # filled after healing skills are created
                    current_build=in_game_build, score_definition=ScoreStaticDefinition(85))

        # -- CORE MIDDLE --
        self.infuse_health_utility: CustomSkillUtilityBase = InfuseHealthUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(8))
        self.protective_spirit_utility: CustomSkillUtilityBase = ProtectiveSpiritUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(7))
        self.spirit_bond_utility: CustomSkillUtilityBase = SpiritBondUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScorePerHealthGravityDefinition(6))
        self.reversal_of_fortune_utility: CustomSkillUtilityBase = ReversalOfFortuneUtility(event_bus=self.event_bus,current_build=in_game_build,score_definition=ScorePerHealthGravityDefinition(5))

        # Now wire the spam utility to use these healing skills
        self.spam_if_mana_low_utility.skills = [self.protective_spirit_utility, self.spirit_bond_utility, self.infuse_health_utility, self.reversal_of_fortune_utility]

        #-- LOW (bonds) --
        # Energy floor: refuse to cast bonds if we don't have enough energy to sustain them
        self.protective_bond_utility: CustomSkillUtilityBase = ProtectiveBondUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(20), mana_required_to_cast=self._ENERGY_FLOOR_FOR_BONDS)
        self.life_bond_utility: CustomSkillUtilityBase = LifeBondUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(20), mana_required_to_cast=self._ENERGY_FLOOR_FOR_BONDS)
        self.life_attunement_utility: CustomSkillUtilityBase = LifeAttunementUtility(event_bus=self.event_bus, current_build=in_game_build, score_definition=ScoreStaticDefinition(20), mana_required_to_cast=self._ENERGY_FLOOR_FOR_BONDS)
        self.vital_blessing_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(event_bus=self.event_bus,skill=CustomSkill("Vital_Blessing"),current_build=in_game_build,score_definition=ScoreStaticDefinition(20), mana_required_to_cast=self._ENERGY_FLOOR_FOR_BONDS)

        # Dismiss bonds EARLIER (35%) — score 84 so it fires before bonds but after ER/AoR
        self.dismiss_buff_if_no_mana_utility: CustomSkillUtilityBase = DismissBuffIfNoManaUtility(event_bus=self.event_bus, skill=CustomSkill("Dismiss_Buff_If_No_Mana"),
                                                                                                  skills_to_dismiss=[self.protective_bond_utility, self.life_attunement_utility, self.life_bond_utility],
                                                                                                  current_build=in_game_build, score_definition=ScoreStaticDefinition(84),
                                                                                                  mana_low_threshold=self._DISMISS_THRESHOLD)
        # -- VERY LOW --

        self.maintain_effect_up_on_player_1: CustomSkillUtilityBase = MaintainEffectUpOnPlayerUtility(event_bus=self.event_bus, skill=CustomSkill("Mainain_Effect_Up_On_Player_1"), skill_to_maintain=CustomSkill("Spirit_Bond"), current_build=in_game_build, score_definition=ScoreStaticDefinition(13))
        self.maintain_effect_up_on_player_2: CustomSkillUtilityBase = MaintainEffectUpOnPlayerUtility(event_bus=self.event_bus, skill=CustomSkill("Mainain_Effect_Up_On_Player_2"), skill_to_maintain=CustomSkill("Protective_Spirit"), current_build=in_game_build, score_definition=ScoreStaticDefinition(13))
        self.maintain_effect_up_on_player_3: CustomSkillUtilityBase = MaintainEffectUpOnPlayerUtility(event_bus=self.event_bus, skill=CustomSkill("Mainain_Effect_Up_On_Player_3"), skill_to_maintain=CustomSkill("Reversal_of_Fortune"), current_build=in_game_build, score_definition=ScoreStaticDefinition(13))
        
        # -- OPTIONAL --
        self.elemental_lord_kurzick_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(event_bus=self.event_bus, skill=CustomSkill("Elemental_Lord_kurzick"), current_build=in_game_build, score_definition=ScoreStaticDefinition(70), mana_required_to_cast=10,allowed_states=[BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO, BehaviorState.FAR_FROM_AGGRO])
        self.elemental_lord_luxon_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(event_bus=self.event_bus, skill=CustomSkill("Elemental_Lord_luxon"), current_build=in_game_build, score_definition=ScoreStaticDefinition(70), mana_required_to_cast=10,allowed_states=[BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO, BehaviorState.FAR_FROM_AGGRO])
        self.burning_speed_utility: CustomSkillUtilityBase = KeepSelfEffectUpUtility(event_bus=self.event_bus, skill=CustomSkill("Burning_Speed"), current_build=in_game_build, score_definition=ScoreStaticDefinition(50), mana_required_to_cast=10,allowed_states=[BehaviorState.IN_AGGRO, BehaviorState.CLOSE_TO_AGGRO, BehaviorState.FAR_FROM_AGGRO])

    @property
    @override
    def additional_autonomous_skills(self) -> list[CustomSkillUtilityBase]:
        base: list[CustomSkillUtilityBase] = super().additional_autonomous_skills
        if self.spam_if_mana_low_utility not in base:  base.append(self.spam_if_mana_low_utility)
        if self.maintain_effect_up_on_player_1 not in base: base.append(self.maintain_effect_up_on_player_1)
        if self.maintain_effect_up_on_player_2 not in base: base.append(self.maintain_effect_up_on_player_2)
        if self.dismiss_buff_if_no_mana_utility not in base: base.append(self.dismiss_buff_if_no_mana_utility)
        return base

    @property
    @override
    def complete_build_with_generic_skills(self) -> bool:
        return False

    @property
    @override
    def custom_skills_in_behavior(self) -> list[CustomSkillUtilityBase]:
        return [

            self.aura_of_restoration_utility,
            self.protective_bond_utility,
            self.burning_speed_utility,
            self.protective_spirit_utility,
            self.elemental_lord_kurzick_utility,
            self.elemental_lord_luxon_utility,
            self.spirit_bond_utility,
            self.vital_blessing_utility,
            self.reversal_of_fortune_utility,
            self.ether_renewal_utility,
            self.life_attunement_utility,
            self.life_bond_utility,
            self.burning_speed_utility,
            self.infuse_health_utility,
        ]

    @property
    @override
    def skills_required_in_behavior(self) -> list[CustomSkill]:
        return [
            self.ether_renewal_utility.custom_skill,
            self.aura_of_restoration_utility.custom_skill,
        ]
