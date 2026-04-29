from .GlobalCache import GLOBAL_CACHE 
#this import need to exists or it will break other imports
#need to adress circular import issues, for the time being we will just import everything here

from .routines_src.Agents import Agents as AgentRoutines
from .routines_src.Items import Items as Items
from .routines_src.Party import Party as PartyRoutines
from .routines_src.Checks import Checks as Checks
from .routines_src.Movement import Movement as MovementRoutines
from .routines_src.Targeting import Targeting as TargetingRoutines
from .routines_src.Transition import Transition as TransitionRoutines
from .routines_src.Sequential import Sequential as SequentialRoutines
from .routines_src.Yield import Yield as YieldRoutines
from .routines_src.BehaviourTrees import BT


class _MultiboxHelper:
    """Exposes _Multibox generator methods for widgets that need Routines.Helpers.Multibox."""
    _METHOD_MAP = {
        "resign_party": "_resignParty",
        "send_dialog_to_target": "_send_dialog_with_target",
        "use_consumable": "_use_consumable_message",
        "equip_item_on_account": "_equip_item_message",
        "equip_item_on_all_accounts": "_equip_item_on_all_accounts_message",
        "load_skill_template_on_account": "_load_skill_template_message",
        "load_skill_template_on_all_accounts": "_load_skill_template_on_all_accounts_message",
        "restock_all_pcons": "_restock_all_pcons_message",
        "restock_conset": "_restock_conset_message",
        "restock_resurrection_scroll": "_restock_resurrection_scroll_message",
        "enable_widget": "_enable_widget_message",
        "disable_widget": "_disable_widget_message",
        "abandon_quest": "_abandon_quest_message",
    }

    def __init__(self):
        from Py4GWCoreLib.botting_src.helpers_src.Multibox import _Multibox
        self._mb = _Multibox.__new__(_Multibox)

    def __getattr__(self, name):
        if name.startswith("_"):
            return getattr(self._mb, name)
        mapped = self._METHOD_MAP.get(name)
        if mapped and hasattr(self._mb, mapped):
            return getattr(self._mb, mapped)
        private_name = f"_{name}"
        if hasattr(self._mb, private_name):
            return getattr(self._mb, private_name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


class _Helpers:
    Multibox = _MultiboxHelper()


class Routines:
    BT = BT
    Agents = AgentRoutines
    Items = Items
    Party = PartyRoutines
    Checks = Checks
    Movement = MovementRoutines
    Transition = TransitionRoutines
    Targeting = TargetingRoutines
    Sequential = SequentialRoutines
    Yield = YieldRoutines
    Helpers = _Helpers
    
