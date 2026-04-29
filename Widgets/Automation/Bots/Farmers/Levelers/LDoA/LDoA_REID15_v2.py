"""
LDoA REID15 v2.0 — Yield/Coroutine Architecture
==================================================
Pre-Searing Ascalon LDoA bot using GLOBAL_CACHE.Coroutines pattern.
Based on Vaettir Bot v3.0 architecture with full inventory management.

Architecture:
- RunBotLogic(): main farm loop coroutine (switches by selected mode)
- CombatHandler(): parallel coroutine for combat during explorable maps
- main(): per-frame entry (DrawWindow + coroutine stepping)

Single account, no heroes (Pre-Searing).
"""

import Py4GW
from Py4GWCoreLib import *
from Py4GWCoreLib import (
    GLOBAL_CACHE, Agent, AgentArray, ItemArray,
    Routines, Range, Map, Player, Party, Quest,
    Timer, ImGui, PyImGui,
    ConsoleLog, ModelID, Utils
)
import time
import random
import traceback
import inspect

MODULE_NAME = "LDoA REID15 v2.0"

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

# Map IDs
ASCALON_CITY    = 148
ASHFORD_ABBEY   = 164
FOIBLES_FAIR    = 165
FORT_RANIK      = 166
BARRADIN_ESTATE = 163

# Quest IDs
CHARR_AT_THE_GATE = 46

# Item Model IDs
IMP_STONE = 30847
WAND_MODEL = 6508
SHIELD_MODEL = 6514
BOW_MODEL = 5831

# Dialog IDs (hex -> int)
DIALOG = {
    'town_crier_quest':    0x805001,
    'tydus_reward':        0x805007,
    'warrior_reward':      0x80DD01,
    'warrior_quest_take':  0x80DD07,
    'warrior_quest_2':     0x805501,
    'warrior_quest_done':  0x805507,
    'ranger_reward':       0x80DE01,
    'ranger_quest_take':   0x80DE07,
    'ranger_quest_2':      0x805601,
    'ranger_quest_done':   0x805607,
    'monk_reward':         0x80DC01,
    'monk_quest_take':     0x80DC07,
    'monk_quest_2':        0x805401,
    'monk_quest_done':     0x805407,
    'necro_reward':        0x80DA01,  # Fixed from v1
    'necro_quest_take':    0x80DA07,  # Fixed from v1
    'necro_quest_2':       0x805201,  # Fixed from v1
    'necro_quest_done':    0x805207,  # Fixed from v1
    'mesmer_reward':       0x80D901,  # Fixed from v1
    'mesmer_quest_take':   0x80D907,  # Fixed from v1
    'mesmer_quest_2':      0x805101,  # Fixed from v1
    'mesmer_quest_done':   0x805107,  # Added from v1
    'ele_reward':          0x80DB01,  # Added from v1
    'ele_quest_take':      0x80DB07,  # Added from v1
    'ele_quest_2':         0x805301,  # Added from v1
    'ele_quest_done':      0x805307,  # Added from v1
    'althea_quest':        0x804703,
    'althea_skills':       0x804701,
    'rurik_quest':         0x802E01,
    'tame_pet_quest':      0x804C03,
    'tame_pet_skills':     0x804C01,
    'tame_pet_accept':     0x85,
    'warrior_skill_quest_1':  0x804B03,
    'warrior_skill_take_1':   0x804B01,
    'warrior_skill_quest_2':  0x803C03,
    'warrior_skill_take_2':   0x803C01,
    'warrior_skill_quest_3':  0x802803,
    'warrior_skill_take_3':   0x802801,
    'ranger_skill_quest_1':   0x802A03,
    'ranger_skill_take_1':    0x802A01,
    'ranger_skill_quest_2':   0x805803,
    'ranger_skill_take_2':    0x805801,
    'monk_skill_quest_1':     0x805703,
    'monk_skill_take_1':      0x805701,
    'monk_skill_quest_2':     0x804A03,
    'monk_skill_take_2':      0x804A01,
    'monk_skill_quest_3':     0x804D03,
    'monk_skill_take_3':      0x804D01,
    'necro_skill_quest_1':    0x804803,
    'necro_skill_take_1':     0x804801,
    'necro_skill_quest_2':    0x802F03,
    'necro_skill_take_2':     0x802F01,
    'necro_skill_quest_3':    0x802B03,
    'necro_skill_take_3':     0x802B01,
    'nicholas_accept':        0x85,
    'nicholas_gift':          0x86,
}

# Nick items to never salvage
NICK_ITEM_MODELS = frozenset({
    ModelID.Spider_Leg.value if hasattr(ModelID, 'Spider_Leg') else 0,
    ModelID.Charr_Carving.value if hasattr(ModelID, 'Charr_Carving') else 0,
    ModelID.Icy_Lodestone.value if hasattr(ModelID, 'Icy_Lodestone') else 0,
    ModelID.Dull_Carapace.value if hasattr(ModelID, 'Dull_Carapace') else 0,
    ModelID.Gargoyle_Skull.value if hasattr(ModelID, 'Gargoyle_Skull') else 0,
    ModelID.Worn_Belt.value if hasattr(ModelID, 'Worn_Belt') else 0,
    ModelID.Unnatural_Seed.value if hasattr(ModelID, 'Unnatural_Seed') else 0,
    ModelID.Skale_Fin_PreSearing.value if hasattr(ModelID, 'Skale_Fin_PreSearing') else 0,
    ModelID.Skeletal_Limb.value if hasattr(ModelID, 'Skeletal_Limb') else 0,
    ModelID.Enchanted_Lodestone.value if hasattr(ModelID, 'Enchanted_Lodestone') else 0,
    ModelID.Grawl_Necklace.value if hasattr(ModelID, 'Grawl_Necklace') else 0,
    ModelID.Baked_Husk.value if hasattr(ModelID, 'Baked_Husk') else 0,
    ModelID.Red_Iris_Flower.value if hasattr(ModelID, 'Red_Iris_Flower') else 0,
    ModelID.Gift_Of_The_Huntsman.value if hasattr(ModelID, 'Gift_Of_The_Huntsman') else 0,
    ModelID.Vial_Of_Dye.value if hasattr(ModelID, 'Vial_Of_Dye') else 0,
} - {0})  # Remove 0s from failed hasattr checks

KIT_MODELS = frozenset({
    ModelID.Salvage_Kit.value if hasattr(ModelID, 'Salvage_Kit') else 2992,
    ModelID.Superior_Identification_Kit.value if hasattr(ModelID, 'Superior_Identification_Kit') else 5899,
})

# ═══════════════════════════════════════════════════════════
# PATHS (ported from v1 — all coordinates preserved)
# ═══════════════════════════════════════════════════════════

# LVL 1 COMMON
PATH_TO_TOWN_CRIER     = [(9800, -453), (9983, -483)]
PATH_TO_SIR_TYDUS      = [(10780, 1039), (11686, 3444)]
PATH_EXIT_ASCALON      = [(7669, 5776), (7416, 5527), (7400, 5450)]
PATH_TO_ALTHEA         = [(5132, 5130), (2785, 7726)]
PATH_LEVELING_LOOP     = [(1787, 6051), (-1598, 5442), (-1504, 3937), (-4862, 4357), (-4780, 6813), (-6099, 5896), (-4443, 7583), (-5463, 11334), (-8548, 11318), (-8749, 7125), (-8766, 5884), (-7931, 4779), (-7064, 2961)]
PATH_TO_RURIK          = [(7555, 10673), (5703, 10663)]
PATH_ASCALON_EXIT      = [(7420, 5450)]

# PROFESSION TRAINERS
PATH_TO_VAN            = [(6435, 4295), (6126, 3997)]
PATH_WARRIOR_QUEST     = [(5639, 2509), (5055, -82), (5793, -3249), (4668, -3311)]
PATH_TO_ARTEMIS        = [(6382, 4349), (6152, 4203)]
PATH_RANGER_QUEST      = [(4873, 1278), (5005, -1706), (5546, -4058)]
PATH_TO_CIGLO          = [(6355, 4323), (6008, 4203)]
PATH_MONK_QUEST_1      = [(4832, -54), (5948, -2922), (6710, -3284), (5894, -4254), (4011, -2569), (3886, -4384)]
PATH_MONK_QUEST_2      = [(4406, -1190), (5229, 1849), (5905, 4164)]
PATH_TO_VERATA         = [(6427, 4368), (6146, 4197)]
PATH_NECRO_QUEST       = [(4807, 1228), (4321, 339)]
PATH_TO_SEBEDOH        = [(6540, 4215), (6232, 3924)]
PATH_MESMER_QUEST      = [(4917, 1313), (4732, 989)]
PATH_TO_HOWLAND        = [(6489, 4341), (6189, 4058)]
PATH_ELE_QUEST         = [(4668, 899), (5061, -3072), (5416, -3868), (4628, -3326), (4912, -2723)]

# LVL 2-10 RURIK FOLLOW
PATH_RURIK_PAUSE       = [(5987, 4087), (5977, 4077)]
PATH_RURIK_FOLLOW      = [(6068, 4180), (5835, 4323), (5602, 4493), (5371, 4672), (5147, 4858), (4933, 5058), (4721, 5255), (4504, 5446), (4283, 5637), (4068, 5825), (3845, 6010), (3577, 6131), (3299, 6199), (3012, 6249), (2720, 6290), (2438, 6324), (2147, 6362), (1860, 6416), (1575, 6433), (1284, 6437), (995, 6441), (702, 6477), (428, 6576), (208, 6759), (-7, 6959), (-221, 7157), (-437, 7358), (-649, 7555), (-861, 7751), (-1080, 7944), (-1301, 8129), (-1522, 8312), (-1750, 8496), (-1997, 8641), (-2260, 8776), (-2519, 8910), (-2753, 9078), (-2902, 9324), (-3020, 9584), (-3137, 9847), (-3252, 10115), (-3367, 10386), (-3486, 10655), (-3724, 10822), (-3981, 10957), (-4241, 11095), (-4483, 11222), (-3823, 11060), (-5253, 11672)]

# LVL 11-20 FARMER HAMNET
PATH_FOIBLE_EXIT       = [(344, 7832), (367, 7790)]
PATH_TO_BANDITS        = [(2312, 5970), (2586, 4429)]

# NICK ITEM FARM PATHS
PATH_DULL_CARAPACES_1  = [(6172, 3424), (5622, -1252), (6846, -4826), (8672, -8150)]
PATH_DULL_CARAPACES_2  = [(9780, -9295), (12204, -8720), (9406, -11301), (6718, -11119), (5545, -9860), (5652, -10396), (3570, -12713), (3338, -14367)]
PATH_GARGOYLE_SKULLS   = [(-6865, 16314), (-6110, 15653), (-3609, 18598), (319, 17870), (4637, 18761), (8681, 18328), (12009, 18320), (8850, 18318), (4803, 18858), (3978, 13475), (2475, 10590), (2363, 8723), (6730, 9137), (7973, 7602), (10277, 7417), (12404, 9434), (10383, 7245), (10153, 6107)]
PATH_GRAWL_NECKLACES   = [(-7907, 1420), (-7604, -174), (-1761, 302), (1534, 427), (3561, 3397), (5011, 4977), (6771, 5189), (4407, 5696), (2897, 6742), (6016, 9555), (5084, 12217), (10102, 10290), (10446, 11629), (10523, 9636), (9650, 12299), (10809, 14616), (11965, 12534), (13720, 11643), (15842, 12116)]
PATH_ICY_LODESTONES    = [(1929, 6567), (2756, 3318), (2925, 685), (801, 2199), (-1758, 4377), (-3870, 3716), (-5544, 1239), (-5822, -46), (-5907, -3149), (-5403, -5352), (-4042, -6547), (-5660, -9938), (-8195, -10859), (-9841, -10663), (-12558, -11765), (-14690, -12464), (-17297, -11145), (-16217, -8138), (-15125, -6660)]
PATH_ENCHANTED_LODE_GO = [(-7218, 1426), (-7400, 1441)]
PATH_ENCHANTED_LODE    = [(-7921, 1440), (-5399, 5794), (-6767, 8561), (-8279, 10731), (-9908, 12031), (-12251, 10228), (-14230, 12048), (-15170, 13810), (-16298, 14700), (-18429, 14048), (-20027, 12365), (-18622, 10544), (-15839, 7664), (-13401, 8732), (-11610, 7356), (-9953, 5829), (-12260, 3482), (-11355, 963), (-11205, -1713), (-8553, -3224), (-10732, -3983), (-13355, -3293), (-8234, -8273), (-5533, -7010), (-1779, -4108), (-1779, -4108), (6834, -1757), (10554, -2699), (10554, -2699), (12037, -716), (9518, -280), (7715, 1582), (10290, 2364), (12597, 4909), (14283, 4709), (13888, 5908), (17120, 3392), (17183, -1059), (15351, -3086), (15434, -87), (13876, -2130), (15506, -6090), (11521, -6408), (8473, -8671), (7724, -4358)]
PATH_RED_IRIS_1        = [(3933, 6375), (-1421, 9928), (-1916, 10016)]
PATH_RED_IRIS_2        = [(-5258, 11577), (-8414, 11233), (-9791, 4834), (-11879, 2053), (-9744, -1949)]
PATH_RED_IRIS_3        = [(-11003, -7776), (-12006, -12989)]
PATH_RED_IRIS_4        = [(-9578, -16011), (-4681, -12246), (-3060, -13281), (-468, -10929)]
PATH_RED_IRIS_5        = [(2432, -8082), (5837, -6406), (9905, -7336), (11280, -6987)]
PATH_GREENHILLS_TO_CAT = [(-5677, 5335), (-5356, 8272), (-3150, 9200)]
PATH_SKELE_LIMBS       = [(-7439, 13679), (-4499, 10305), (-4468, 8420), (-6126, 8229), (-7305, 9075), (-8043, 9784), (-9305, 10684), (-11632, 10827), (-12712, 9802), (-13320, 9242), (-13514, 7688), (-13335, 6960), (-12263, 5545), (-11292, 5096), (-9862, 4795), (-8895, 5093), (-8260, 5806), (-7267, 6571)]
PATH_SKALE_FINS        = [(22578, 6846), (22323, 4048), (21443, 1909), (18033, 2908), (16335, 3362), (15881, 7289), (16861, 6427), (15501, 2865), (13711, 2461), (13511, 1802), (15186, 39), (17397, 903), (19531, 946)]
PATH_RANIK_EXIT        = [(22858, 10512), (22554, 8048), (22529, 7569), (22527, 7450)]
PATH_SPIDER_LEGS_1     = [(22532, 5576), (21978, 3604), (19968, 1388), (18462, -1529), (18444, -3023), (19911, -5170), (20548, -6388), (21722, -7002)]
PATH_SPIDER_LEGS_2     = [(21072, -6376), (19635, -4807), (17780, -3376), (16003, -5076), (15449, -8006), (17323, -11240), (18931, -12260), (19979, -12162), (21141, -12814), (21915, -12167), (21007, -11370), (18961, -12306), (17074, -10430), (14527, -10426), (12768, -10614), (10277, -11768), (8869, -11638), (8172, -12088), (7111, -12836), (4879, -13254), (2820, -12874), (1823, -12239), (637, -11093), (-190, -10429), (2104, -10838), (3435, -8681), (5221, -5253), (5163, -3186), (5661, -2630), (7170, -3541), (7845, -8356), (9010, -9787), (7856, -10328)]
PATH_UNNATURAL_SEEDS   = [(-7910, 1418), (-7555, -1752), (-8062, -3071), (-12424, -3125), (-10948, -5876), (-8324, -8323), (-13903, -2822), (-14847, -707), (-15180, 2976), (-16849, 7044), (-20397, 8233), (-20550, 6263), (-20964, 3164), (-18933, 3676), (-17504, 3399), (-20758, -1853), (-20250, -5231), (-19147, -6511), (-16715, -7669), (-16317, -9650), (-16739, -11548), (-12792, -14235), (-15378, -15096)]
PATH_WORN_BELTS        = [(-7926, 1415), (-11055, 1227), (-12315, -2885), (-14281, -2275), (-15024, 1351), (-16472, 6562), (-18774, 8704), (-20803, 9038), (-20203, 7347), (-19635, 5800), (-18700, 2386), (-20420, 1201), (-21388, -174), (-18041, -1241), (-18791, -3534), (-20430, -6529), (-17984, -6975), (-21071, -8679), (-17327, -10461), (-16030, -11243), (-16017, -14750), (-14710, -15286), (-11258, -14416), (-10075, -14528), (-7392, -12948), (-5987, -15648), (-10315, -14775), (-14548, -13886), (-17675, -12879), (-18913, -11854), (-20070, -13160), (-20386, -14388), (-22363, -15209), (-22475, -12587)]
PATH_BAKED_HUSKS       = [(-10220, -5000), (-10507, -3583), (-10813, -1725), (-11045, -485), (-6711, -1690)]
PATH_NICHOLAS_SANDFORD = [(22237, 4061), (17213, 2817), (16320, 3621), (16757, 8255), (17315, 8812), (14385, 11414), (14159, 13931), (15257, 16442)]

# CHARR GATE OPENER
PATH_GATE_OPENER_1     = [(6602, 4485)]
PATH_GATE_OPENER_2     = [(3220, 6900), (-1681, 11876), (-5475, 12865)]
PATH_GATE_FAST_RUN     = [(-5390, 12810), (-5258, 12851), (-5448, 12353), (-5575, 13600), (-5535, 13800)]

# TRAVEL PATHS (town-to-town)
PATH_ABBEY_OUT         = [(7954, 5862), (7430, 5480), (7410, 5480)]
PATH_ABBEY_WALK        = [(3386, -1112), (-220, -5393), (-3710, -6761), (-6529, -6570), (-11028, -6230), (-11060, -6200)]
PATH_FOIBLE_OUT        = [(-11436, -6243), (-11400, -6250)]
PATH_BARRADIN_OUT      = [(7954, 5862), (7430, 5480), (7410, 5480)]
PATH_BARRADIN_GO       = [(-7218, 1426), (-7400, 1441)]

# TAME PET
PATH_ASHFORD_OUT       = [(-11962, -6244), (-11445, -6239), (-11400, -6273)]
PATH_TAME_PET_1        = [(-6395, -7016), (-4560, -12326), (1040, -16292), (4070, -19779), (4200, -19700)]
PATH_TAME_PET_2        = [(-15573, 14259), (-17445, 11742), (-17017, 10411)]
PATH_TAME_PET_3        = [(-17263, 10142), (-18774, 8436), (-20164, 6922), (-19424, 4220), (-14998, -475)]

# TRAVEL BETWEEN OUTPOSTS
PATH_FOIBLE_WALK_1     = [(-10981, -7090), (-11122, -8523), (-11432, -9934), (-11764, -11343), (-12125, -12705), (-12434, -14111), (-12674, -15526), (-12195, -16875), (-11589, -18186), (-11426, -19353), (-12765, -19885), (-13353, -20080), (-13900, -20115)]
PATH_FOIBLE_WALK_2     = [(9449, 19702), (8930, 18412), (8359, 17098), (7452, 16009), (6267, 15195), (5451, 14006), (4664, 12799), (3873, 11587), (3097, 10374), (2432, 9087), (1727, 7834), (724, 7238), (533, 7395), (400, 7600)]
PATH_RANIK_WALK_1      = [(-10932, -6205), (-9515, -6346), (-8090, -6555), (-6729, -7002), (-6127, -8304), (-5594, -9649), (-5082, -10996), (-4517, -12318), (-3366, -13136), (-2190, -13980), (-1022, -14822), (135, -15678), (1116, -16709), (1898, -17926), (2973, -18873), (4168, -19684), (4021, -19758), (4200, -19700)]
PATH_RANIK_WALK_2      = [(-14893, 16791), (-14206, 15600), (-13866, 15095), (-12586, 14586), (-11475, 13725), (-10498, 12872), (-9315, 12098), (-8090, 11395), (-6881, 10672), (-5675, 9957), (-4718, 9350), (-3505, 8623), (-800, 7122), (255, 6260), (736, 6445), (2141, 6244), (3407, 5858), (4699, 5564), (6082, 5353), (6991, 4725), (7820, 3665), (8972, 2951), (10137, 2640), (11540, 2574), (12891, 2200), (15271, 1458), (16644, 1483), (18046, 1557), (18737, 1920), (19933, 2595), (21107, 3367), (22013, 4355), (22253, 5513), (22309, 6178), (22547, 7142), (22500, 7100)]
PATH_BARRADIN_WALK_1   = [(6558, 4489), (5233, 5033), (3979, 5754), (2732, 6470), (1491, 7069), (258, 7412), (-962, 8152), (-2149, 8973), (-3284, 9842), (-3890, 10510), (-5070, 11302), (-6496, 11491), (-7890, 11304), (-8648, 10100), (-9374, 8859), (-10468, 7955), (-11728, 8344), (-12897, 9191), (-14117, 9957), (-14600, 10060)]
PATH_BARRADIN_WALK_2   = [(21932, 12966), (20720, 13561), (19394, 13174), (18149, 12450), (17131, 11462), (16396, 10223), (15660, 8984), (14871, 7778), (14080, 6573), (13372, 5317), (12640, 4076), (11617, 3237), (10499, 2614), (10646, 1180), (11326, -16), (11564, -1417), (10516, -2215), (9233, -2842), (7825, -3180), (6458, -3523), (5016, -3411), (3579, -3350), (2140, -3313), (696, -3293), (-512, -2603), (-1779, -1983), (-2953, -1141), (-4317, -695), (-5735, -423), (-7139, -154), (-7815, 751), (-7511, 1442), (-7310, 1456)]

# SKILL QUEST PATHS
PATH_WARRIOR_SKILL_1   = [(21932, 12966), (21258, 13066)]
PATH_WARRIOR_SKILL_2   = [(-6407, 1383), (-6475, 1551)]
PATH_RANGER_SKILL_1    = [(-13152, 1449), (-10295, 3989), (-6644, 5097), (-4889, 5031), (-4455, 5902)]
PATH_RANGER_SKILL_2    = [(1618, 7084), (2605, 9654), (4034, 10546), (7977, 9735), (11355, 6369), (11250, 2739), (11274, -2882), (13194, -7177), (12257, -10820), (13254, -15838), (16327, -17666)]
PATH_RANGER_SKILL_3    = [(17195, -17559), (18077, -17350)]
PATH_MONK_SKILL_1      = [(-12786, -6952), (-12209, -8014)]
PATH_MONK_SKILL_2      = [(20844, 13667), (19248, 13103), (17793, 12599)]
PATH_NECRO_SKILL_1     = [(-13247, -7111), (-13610, -7095), (-13900, -7075)]
PATH_NECRO_SKILL_2     = [(13776, 2598), (13775, 3515)]
PATH_NECRO_SKILL_3     = [(13755, 2156), (13580, -344), (10919, -230), (10595, -123), (9375, -1084), (9621, -4537), (7041, -5399), (7163, -7846), (5923, -8610), (3219, -9292), (1139, -8041), (-190, -9648), (-1397, -11243), (-3260, -9333), (-3905, -8209), (-8007, -7860), (-7836, -5523), (-6484, -3899), (-7420, -2054), (-10793, -1681), (-10772, 4092)]
PATH_NECRO_SKILL_4     = [(20635, 13722), (19086, 12838), (17482, 12210)]

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

class InventorySettings:
    def __init__(self):
        self.leave_free_slots = 3
        self.keep_id_kits = 1
        self.keep_salvage_kits = 2
        self.salvage_whites = True
        self.salvage_blues = False    # Safe default for Pre-Searing
        self.salvage_purples = False
        self.salvage_golds = False

class CombatSettings:
    def __init__(self):
        self.skill_interval = 1.5
        self.enemy_range = 1200
        self.health_flee_pct = 0.45
        self.loot_range = 1200

# ═══════════════════════════════════════════════════════════
# STATISTICS
# ═══════════════════════════════════════════════════════════

class RunStatistics:
    def __init__(self):
        self.global_timer = Timer()
        self.runs = 0
        self.deaths = 0
        self.xp_at_start = 0
        self.xp_tracking_init = False

    def init_xp(self):
        if not self.xp_tracking_init:
            self.xp_at_start = Player.GetExperience()
            self.xp_tracking_init = True

    def get_xp_gained(self):
        return Player.GetExperience() - self.xp_at_start

    def get_xp_per_hour(self):
        elapsed = self.global_timer.GetElapsedTime() / 1000.0  # ms -> sec
        if elapsed < 30:
            return 0
        return int(self.get_xp_gained() / (elapsed / 3600))

# ═══════════════════════════════════════════════════════════
# BOT STATE
# ═══════════════════════════════════════════════════════════

class BotState:
    def __init__(self):
        self.is_running = False
        self.current_step = "Idle"
        self.selected_mode = 0  # Radio button selection
        self.log = True

        self.combat = CombatSettings()
        self.inventory = InventorySettings()
        self.stats = RunStatistics()

        self.window_module = ImGui.WindowModule(
            MODULE_NAME, window_name=MODULE_NAME,
            window_size=(350, 400),
            window_flags=PyImGui.WindowFlags.AlwaysAutoResize
        )

        # Nick item tracking
        self.tracked_items = {
            ModelID.Vial_Of_Dye: "Vials of Dye",
            ModelID.Spider_Leg: "Spider Legs",
            ModelID.Charr_Carving: "Charr Carvings",
            ModelID.Icy_Lodestone: "Icy Lodestones",
            ModelID.Dull_Carapace: "Dull Carapaces",
            ModelID.Gargoyle_Skull: "Gargoyle Skulls",
            ModelID.Worn_Belt: "Worn Belts",
            ModelID.Unnatural_Seed: "Unnatural Seeds",
            ModelID.Skale_Fin_PreSearing: "Skale Fins",
            ModelID.Skeletal_Limb: "Skeletal Limbs",
            ModelID.Enchanted_Lodestone: "Enchanted Lodestones",
            ModelID.Grawl_Necklace: "Grawl Necklaces",
            ModelID.Baked_Husk: "Baked Husks",
            ModelID.Red_Iris_Flower: "Red Iris Flowers",
            ModelID.Gift_Of_The_Huntsman: "Gifts of the Huntsman",
        }
        self.initial_quantities = {}

bot = BotState()

# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def jitter(base_ms, variance=0.15):
    """Human-like timing variance (+/-15%)."""
    offset = base_ms * variance
    return int(base_ms + random.uniform(-offset, offset))

def is_dead():
    return Agent.IsDead(Player.GetAgentID())

def is_low_health():
    return Agent.GetHealth(Player.GetAgentID()) < bot.combat.health_flee_pct

def is_outpost():
    mid = Map.GetMapID()
    return mid in (ASCALON_CITY, ASHFORD_ABBEY, FOIBLES_FAIR, FORT_RANIK, BARRADIN_ESTATE)

def is_ranger():
    p, s = Agent.GetProfessionIDs(Player.GetAgentID())
    return p == Profession.Ranger.value or s == Profession.Ranger.value

def get_self_heal_slots():
    """Return likely heal skill slots for current profession."""
    p, _ = Agent.GetProfessionIDs(Player.GetAgentID())
    if p == Profession.Warrior.value: return [4]
    if p == Profession.Ranger.value: return [3]
    if p == Profession.Monk.value: return [3, 4]
    if p == Profession.Necromancer.value: return [1]
    if p == Profession.Mesmer.value: return [3]
    if p == Profession.Elementalist.value: return [3]
    return []

# ═══════════════════════════════════════════════════════════
# INVENTORY MANAGEMENT (Yield coroutines)
# ═══════════════════════════════════════════════════════════

def filter_salvage_array():
    """Get salvageable items filtered by settings. Never salvages Nick items."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    items = ItemArray.GetItemArray(bags)
    # Must be salvageable
    items = ItemArray.Filter.ByCondition(items, lambda i: Item.Usage.IsSalvageable(i))
    # Must be identified (or white)
    items = ItemArray.Filter.ByCondition(items,
        lambda i: Item.Usage.IsIdentified(i) or Item.Rarity.IsWhite(i))
    # Never salvage Nick collectibles or kits
    items = ItemArray.Filter.ByCondition(items,
        lambda i: Item.GetModelID(i) not in NICK_ITEM_MODELS)
    items = ItemArray.Filter.ByCondition(items,
        lambda i: Item.GetModelID(i) not in KIT_MODELS)
    # Rarity filters
    cfg = bot.inventory
    if not cfg.salvage_whites:
        items = ItemArray.Filter.ByCondition(items, lambda i: not Item.Rarity.IsWhite(i))
    if not cfg.salvage_blues:
        items = ItemArray.Filter.ByCondition(items, lambda i: not Item.Rarity.IsBlue(i))
    if not cfg.salvage_purples:
        items = ItemArray.Filter.ByCondition(items, lambda i: not Item.Rarity.IsPurple(i))
    if not cfg.salvage_golds:
        items = ItemArray.Filter.ByCondition(items, lambda i: not Item.Rarity.IsGold(i))
    return items

def filter_identify_array():
    """Get unidentified items (skip whites, they don't need ID)."""
    bags = ItemArray.CreateBagList(1, 2, 3, 4)
    items = ItemArray.GetItemArray(bags)
    items = ItemArray.Filter.ByCondition(items, lambda i: not Item.Usage.IsIdentified(i))
    items = ItemArray.Filter.ByCondition(items, lambda i: not Item.Rarity.IsWhite(i))
    return items

def needs_inventory_handling():
    """Check if we need to visit merchant for inventory management."""
    if Inventory.GetFreeSlotCount() < bot.inventory.leave_free_slots:
        return True
    if len(filter_identify_array()) > 0:
        return True
    if len(filter_salvage_array()) > 0:
        return True
    return False

def InventoryHandler():
    """Yield coroutine: ID, salvage in field. No merchant needed for basic ops."""
    log = bot.log

    # Identify items
    to_id = filter_identify_array()
    if to_id:
        bot.current_step = "Identifying items"
        # DISABLED: InventoryPlus handles identify
        # yield from Routines.Yield.Items.IdentifyItems(to_id, log)

    # Salvage (whites only by default -- safe for Pre-Searing)
    to_salv = filter_salvage_array()
    if to_salv:
        bot.current_step = "Salvaging items"
        # DISABLED: InventoryPlus handles salvage
        # yield from Routines.Yield.Items.SalvageItems(to_salv, log)

    yield

# ═══════════════════════════════════════════════════════════
# COMBAT (Yield coroutine helpers)
# ═══════════════════════════════════════════════════════════

def pick_best_target(enemies):
    """Smart targeting: wounded > casters > nearest."""
    if not enemies:
        return 0
    wounded = AgentArray.Filter.ByCondition(enemies, lambda e: Agent.GetHealth(e) < 0.5)
    if wounded:
        return AgentArray.Sort.ByHealth(wounded)[0]
    try:
        casters = AgentArray.Filter.ByCondition(enemies, lambda e: Agent.IsCaster(e))
        if casters:
            return AgentArray.Sort.ByDistance(casters, Player.GetXY())[0]
    except Exception:
        pass
    return enemies[0]

def use_best_skill(target_id, max_slot=8):
    """Smart skill usage with self-heal priority."""
    my_id = Player.GetAgentID()
    if Agent.IsCasting(my_id) or Agent.IsKnockedDown(my_id):
        return False

    Player.ChangeTarget(target_id)

    # Self-heal when low
    if Agent.GetHealth(my_id) < 0.6:
        for slot in get_self_heal_slots():
            if slot <= max_slot:
                skill_id = SkillBar.GetSkillIDBySlot(slot)
                if skill_id and skill_id != 0:
                    energy_cost = Skill.Data.GetEnergyCost(skill_id) or 0
                    my_energy = Agent.GetEnergy(my_id) * Agent.GetMaxEnergy(my_id)
                    if my_energy >= energy_cost:
                        SkillBar.UseSkill(slot, my_id)
                        return True

    # Damage skills
    for slot in range(1, max_slot + 1):
        skill_id = SkillBar.GetSkillIDBySlot(slot)
        if skill_id and skill_id != 0:
            energy_cost = Skill.Data.GetEnergyCost(skill_id) or 0
            my_energy = Agent.GetEnergy(my_id) * Agent.GetMaxEnergy(my_id)
            if my_energy >= energy_cost:
                SkillBar.UseSkill(slot, target_id)
                return True

    # Auto-attack
    Player.Interact(target_id, call_target=False)
    return False

def combat_loop(path_points, enemy_range=1200, max_slot=8, loot=True,
                aggro_only=False, exit_condition=None):
    """Yield coroutine: follow path with combat, looting, and smart targeting.
    This is the core farming loop used by all routes."""

    for wx, wy in path_points:
        # Add human-like jitter to waypoints
        jx = wx + random.uniform(-25, 25)
        jy = wy + random.uniform(-25, 25)

        # Move toward waypoint
        Player.Move(jx, jy)
        move_start = time.time()

        while True:
            if exit_condition and exit_condition():
                return
            if is_dead():
                return
            if Map.IsMapLoading():
                return

            my_xy = Player.GetXY()
            dist = Utils.Distance(my_xy, (jx, jy))

            # Arrived at waypoint
            if dist < 150:
                break

            # Stuck detection (10s no movement)
            if time.time() - move_start > 12:
                ConsoleLog(MODULE_NAME, "Stuck - skipping waypoint")
                break

            # Check for enemies
            enemies = AgentArray.GetEnemyArray()
            enemies = AgentArray.Filter.ByDistance(enemies, my_xy, enemy_range)
            enemies = AgentArray.Filter.ByAttribute(enemies, 'IsAlive')
            if aggro_only:
                enemies = AgentArray.Filter.ByAttribute(enemies, 'IsAgressive')
            enemies = AgentArray.Sort.ByDistance(enemies, my_xy)

            if enemies:
                target = pick_best_target(enemies)
                if target:
                    if is_ranger():
                        Party.Pets.SetPetBehavior(0, target)
                    Player.Interact(target, call_target=False)

                    # Fight until target dead
                    fight_start = time.time()
                    while Agent.IsAlive(target) and time.time() - fight_start < 30:
                        if is_dead():
                            return
                        use_best_skill(target, max_slot)
                        yield from Routines.Yield.wait(jitter(int(bot.combat.skill_interval * 1000)))

                    # Brief post-combat pause (human-like)
                    if random.random() < 0.08:
                        yield from Routines.Yield.wait(jitter(1500))
                    continue  # Re-check for more enemies before moving

            # Loot between fights
            if loot and not enemies:
                loot_items = AgentArray.GetItemArray()
                loot_items = AgentArray.Filter.ByDistance(loot_items, my_xy, bot.combat.loot_range)
                loot_items = AgentArray.Filter.ByCondition(
                    loot_items, lambda aid: Agent.GetItemAgentOwnerID(aid) in (Player.GetAgentID(), 0))
                loot_items = AgentArray.Sort.ByDistance(loot_items, my_xy)
                if loot_items:
                    Player.Interact(loot_items[0])
                    yield from Routines.Yield.wait(jitter(600))
                    continue

            # Pet guard when idle
            if is_ranger() and not enemies:
                Party.Pets.SetPetBehavior(1, Player.GetAgentID())

            yield from Routines.Yield.wait(jitter(200))

# ═══════════════════════════════════════════════════════════
# FARM ROUTINES (Yield coroutines)
# ═══════════════════════════════════════════════════════════

def RunCharrAtTheGate():
    """Level 2-10: Charr at the Gate with Rurik escort, looping."""
    log = bot.log
    while bot.is_running:
        bot.current_step = "Traveling to Ascalon City"
        yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
        yield from Routines.Yield.wait(jitter(2000))

        bot.current_step = "Exiting to explorable"
        yield from Routines.Yield.Movement.FollowPath(
            PATH_ASCALON_EXIT, lambda: Map.IsExplorable() or not bot.is_running)
        yield from Routines.Yield.Map.WaitforMapLoad(0, log)
        yield from Routines.Yield.wait(jitter(1500))

        bot.current_step = "Using Fire Imp"
        Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
        yield from Routines.Yield.wait(jitter(1000))

        bot.current_step = "Following Rurik"
        yield from Routines.Yield.Movement.FollowPath(
            PATH_RURIK_FOLLOW, lambda: is_dead() or not bot.is_running)

        bot.current_step = "Waiting for combat to end"
        yield from Routines.Yield.wait(jitter(5000))

        # Loot drops
        bot.current_step = "Looting"
        loot_items = AgentArray.GetItemArray()
        loot_items = AgentArray.Filter.ByDistance(loot_items, Player.GetXY(), 1500)
        if loot_items:
            yield from Routines.Yield.wait(jitter(1000))

        bot.stats.runs += 1
        bot.current_step = "Run complete"
        yield from Routines.Yield.wait(jitter(500))

def RunFarmerHamnet():
    """Level 11-20: Farmer Hamnet bandit farming from Foible's Fair, looping."""
    log = bot.log
    while bot.is_running:
        bot.current_step = "Traveling to Foible's Fair"
        yield from Routines.Yield.Map.TravelToOutpost(FOIBLES_FAIR, log)
        yield from Routines.Yield.wait(jitter(2000))

        bot.current_step = "Exiting to explorable"
        yield from Routines.Yield.Movement.FollowPath(
            PATH_FOIBLE_EXIT, lambda: Map.IsExplorable() or not bot.is_running)
        yield from Routines.Yield.Map.WaitforMapLoad(0, log)
        yield from Routines.Yield.wait(jitter(1500))

        bot.current_step = "Using Fire Imp"
        Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
        yield from Routines.Yield.wait(jitter(1000))

        bot.current_step = "Fighting bandits"
        yield from combat_loop(PATH_TO_BANDITS, max_slot=4,
                               exit_condition=lambda: is_low_health() or not bot.is_running)

        # Inventory management between runs
        if needs_inventory_handling():
            yield from InventoryHandler()

        bot.stats.runs += 1
        bot.current_step = "Run complete"
        yield from Routines.Yield.wait(jitter(500))

def RunNickItemFarm(path_list, outpost=ASCALON_CITY, district_switch=False,
                    district_outpost=None, district_num=6, aggro_only=False,
                    loot_range=1200):
    """Generic Nick item farming coroutine. Works for all collectible routes."""
    log = bot.log
    while bot.is_running:
        # Travel to starting outpost
        if district_switch and district_outpost:
            bot.current_step = f"Switching district"
            Map.TravelToDistrict(district_outpost, district_num, 0)
            yield from Routines.Yield.wait(jitter(3000))
        else:
            bot.current_step = "Traveling to outpost"
            yield from Routines.Yield.Map.TravelToOutpost(outpost, log)
            yield from Routines.Yield.wait(jitter(2000))

        # Exit to explorable
        bot.current_step = "Entering explorable area"
        # Wait for map to be ready
        while not Map.IsMapReady() or not is_outpost():
            yield from Routines.Yield.wait(500)
            if not bot.is_running:
                return

        yield from Routines.Yield.wait(jitter(1000))

        bot.current_step = "Using Fire Imp"
        Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
        yield from Routines.Yield.wait(jitter(1000))

        bot.current_step = "Farming route"
        for path_segment in path_list:
            if not bot.is_running:
                return
            yield from combat_loop(path_segment, loot=True, aggro_only=aggro_only,
                                   exit_condition=lambda: not bot.is_running)

        # Inventory management between runs
        if needs_inventory_handling():
            yield from InventoryHandler()

        bot.stats.runs += 1
        bot.current_step = "Run complete"
        yield from Routines.Yield.wait(jitter(500))

# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# HELPER: Interact with nearest quest NPC
# ═══════════════════════════════════════════════════════════

def interact_quest_npc():
    """Find nearest NPC with quest marker and interact."""
    my_xy = Player.GetXY()
    allies = AgentArray.GetNPCMinipetArray()
    allies = AgentArray.Filter.ByDistance(allies, my_xy, 5000)
    quest_npcs = [a for a in allies if Agent.HasQuest(a)]
    if quest_npcs:
        Player.Interact(quest_npcs[0], call_target=False)


def interact_nearest_neutral():
    """Find nearest neutral agent and interact (for pet)."""
    neutrals = AgentArray.GetNeutralArray()
    neutrals = AgentArray.Filter.ByDistance(neutrals, Player.GetXY(), 5000)
    neutrals = AgentArray.Sort.ByDistance(neutrals, Player.GetXY())
    if neutrals:
        Player.Interact(neutrals[0])


def use_skill_on_nearest_neutral(skill_id):
    """Use a skill by ID on nearest neutral (Charm Animal on pet)."""
    neutrals = AgentArray.GetNeutralArray()
    neutrals = AgentArray.Filter.ByDistance(neutrals, Player.GetXY(), 5000)
    neutrals = AgentArray.Sort.ByDistance(neutrals, Player.GetXY())
    if not neutrals:
        return
    target = neutrals[0]
    for slot in range(1, 9):
        if SkillBar.GetSkillIDBySlot(slot) == skill_id:
            SkillBar.UseSkill(slot, target)
            return


# ═══════════════════════════════════════════════════════════
# CHARR GATE OPENER (Yield coroutine)
# ═══════════════════════════════════════════════════════════

def RunCharrGateOpener():
    """Open the Charr gate: district switch, exit Ascalon, walk to lever,
    interact, run through gate into Northlands."""
    log = bot.log

    bot.current_step = "Switching district"
    Map.TravelToDistrict(ASCALON_CITY, 6, 0)
    yield from Routines.Yield.wait(jitter(2000))
    while not (Map.IsMapReady() and is_outpost()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASCALON_EXIT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Walking to lever"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_GATE_OPENER_2, lambda: not bot.is_running)

    bot.current_step = "Stabilizing before lever"
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Interacting with lever"
    Keystroke.PressAndRelease(Key.Semicolon.value)
    yield from Routines.Yield.wait(50)
    Keystroke.PressAndRelease(Key.Space.value)
    yield from Routines.Yield.wait(50)

    bot.current_step = "Running through gate"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_GATE_FAST_RUN, lambda: not bot.is_running)

    bot.current_step = "Waiting for Northlands"
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Charr Gate opened"


# ═══════════════════════════════════════════════════════════
# TAME PET (Yield coroutine)
# ═══════════════════════════════════════════════════════════

def RunTamePet():
    """Walk from Ashford Abbey through Regent Valley, get Charm Animal
    quest, tame a pet, return to Ashford Abbey."""
    log = bot.log

    bot.current_step = "Exiting Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASHFORD_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Walking to Regent Valley"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_1, lambda: not bot.is_running)

    # Zone transition
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Walking to pet trainer"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_2, lambda: not bot.is_running)

    bot.current_step = "Interacting with quest NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Taking Charm Animal quest"
    Player.SendDialog(0x804C03)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "Accepting skills"
    Player.SendDialog(0x804C01)
    yield from Routines.Yield.wait(jitter(1500))

    Player.SendDialog(0x85)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "Walking to pet"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_3, lambda: not bot.is_running)

    bot.current_step = "Approaching pet"
    interact_nearest_neutral()
    yield from Routines.Yield.wait(jitter(3000))

    bot.current_step = "Using Charm Animal"
    use_skill_on_nearest_neutral(411)  # Charm Animal skill ID
    yield from Routines.Yield.wait(jitter(20000))

    bot.current_step = "Returning to Ashford Abbey"
    yield from Routines.Yield.Map.TravelToOutpost(ASHFORD_ABBEY, log)
    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Tame Pet complete"


# ═══════════════════════════════════════════════════════════
# TRAVEL ROUTINES (Yield coroutines)
# ═══════════════════════════════════════════════════════════

def TravelToAshfordAbbey():
    """Travel from Ascalon City -> explorable -> Ashford Abbey."""
    log = bot.log

    bot.current_step = "Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Walking to Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_WALK, lambda: is_outpost() or not bot.is_running)

    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Arrived at Ashford Abbey"


def TravelToFoiblesFair():
    """Travel from Ashford Abbey -> Wizard's Folly -> Foible's Fair."""
    log = bot.log

    bot.current_step = "Exiting Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASHFORD_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Walking to Wizard's Folly"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_FOIBLE_WALK_1, lambda: not bot.is_running)

    # Zone transition (Wizard's Folly)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Walking to Foible's Fair"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_FOIBLE_WALK_2, lambda: is_outpost() or not bot.is_running)

    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Arrived at Foible's Fair"


def TravelToFortRanik():
    """Travel from Ashford Abbey -> Regent Valley -> Fort Ranik."""
    log = bot.log

    bot.current_step = "Exiting Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASHFORD_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Walking to Regent Valley"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_1, lambda: not bot.is_running)

    # Zone transition
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Walking to Fort Ranik"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_RANIK_WALK_2, lambda: is_outpost() or not bot.is_running)

    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Arrived at Fort Ranik"


def TravelToBarradinEstate():
    """Travel from Ascalon City -> Green Hills -> Barradin Estate."""
    log = bot.log

    bot.current_step = "Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Abandoning Charr quest"
    Quest.AbandonQuest(CHARR_AT_THE_GATE)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Walking through Green Hills"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_1, lambda: not bot.is_running)

    # Zone transition
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Walking to Barradin Estate"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_2, lambda: is_outpost() or not bot.is_running)

    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Arrived at Barradin Estate"


# ═══════════════════════════════════════════════════════════
# GRAND TOUR (Yield coroutine)
# ═══════════════════════════════════════════════════════════

def RunGrandTour():
    """Visit all Pre-Searing outposts: Ascalon -> Abbey -> Foible's ->
    Ranik -> back to Ascalon -> Barradin Estate."""
    log = bot.log

    # --- Ascalon City -> Ashford Abbey ---
    bot.current_step = "GT: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Walking to Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_WALK, lambda: is_outpost() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    # --- Ashford Abbey -> Foible's Fair ---
    bot.current_step = "GT: Exiting Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASHFORD_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Walking to Wizard's Folly"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_FOIBLE_WALK_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Walking to Foible's Fair"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_FOIBLE_WALK_2, lambda: is_outpost() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    # --- Foible's Fair -> back to Ashford Abbey -> Fort Ranik ---
    bot.current_step = "GT: Traveling to Ashford Abbey"
    yield from Routines.Yield.Map.TravelToOutpost(ASHFORD_ABBEY, log)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Exiting Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASHFORD_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Walking to Regent Valley"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Walking to Fort Ranik"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_RANIK_WALK_2, lambda: is_outpost() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    # --- Fort Ranik -> Ascalon City -> Barradin Estate ---
    bot.current_step = "GT: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Abandoning Charr quest"
    Quest.AbandonQuest(CHARR_AT_THE_GATE)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Walking through Green Hills"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "GT: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "GT: Walking to Barradin Estate"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_2, lambda: is_outpost() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "Grand Tour complete"


# ═══════════════════════════════════════════════════════════
# SKILL UNLOCK QUESTS (Yield coroutines)
# ═══════════════════════════════════════════════════════════

def RunWarriorSkillReq():
    """Warrior skill unlock: Ascalon -> abandon Charr quest -> exit ->
    Green Hills -> pick up 3 quests at trainers in Barradin Estate area."""
    log = bot.log

    bot.current_step = "WR: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "WR: Abandoning Charr quest"
    Quest.AbandonQuest(CHARR_AT_THE_GATE)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "WR: Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "WR: Walking through Green Hills"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "WR: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    # Quest 1: Walk to first trainer
    bot.current_step = "WR: Walking to quest NPC 1"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_WARRIOR_SKILL_1, lambda: not bot.is_running)

    bot.current_step = "WR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x804B03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x804B01)
    yield from Routines.Yield.wait(jitter(3000))

    # Quest 2: Travel to Barradin Estate, walk to second trainer
    bot.current_step = "WR: Traveling to Barradin Estate"
    yield from Routines.Yield.Map.TravelToOutpost(BARRADIN_ESTATE, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "WR: Walking to quest NPC 2"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_WARRIOR_SKILL_2, lambda: not bot.is_running)

    bot.current_step = "WR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x803C03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x803C01)
    yield from Routines.Yield.wait(jitter(3000))

    # Quest 3: Walk to third trainer (same area)
    bot.current_step = "WR: Walking to quest NPC 3"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_WARRIOR_SKILL_2, lambda: not bot.is_running)

    bot.current_step = "WR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x802803)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x802801)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "WR: Closing dialogs"
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "Warrior skill quests complete"


def RunRangerSkillReq():
    """Ranger skill unlock: Ascalon -> Abbey -> tame pet -> pick up
    ranger quests across multiple zones."""
    log = bot.log

    bot.current_step = "RR: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "RR: Traveling to Ashford Abbey"
    yield from Routines.Yield.Map.TravelToOutpost(ASHFORD_ABBEY, log)
    yield from Routines.Yield.wait(jitter(1000))

    # Exit Abbey -> walk to Regent Valley for tame pet
    bot.current_step = "RR: Exiting Ashford Abbey"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ASHFORD_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "RR: Walking to Regent Valley"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "RR: Walking to pet trainer"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_2, lambda: not bot.is_running)

    bot.current_step = "RR: Taking Charm Animal quest"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x804C03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x804C01)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x85)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "RR: Walking to pet"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_TAME_PET_3, lambda: not bot.is_running)

    bot.current_step = "RR: Approaching pet"
    interact_nearest_neutral()
    yield from Routines.Yield.wait(jitter(3000))

    bot.current_step = "RR: Using Charm Animal"
    use_skill_on_nearest_neutral(411)
    yield from Routines.Yield.wait(jitter(20000))

    bot.current_step = "RR: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    # Ranger quest 1
    bot.current_step = "RR: Walking to ranger quest NPC 1"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_RANGER_SKILL_1, lambda: not bot.is_running)

    bot.current_step = "RR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x802A03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x802A01)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "RR: Closing dialogs"
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    # Travel to Foible's Fair -> exit -> walk to ranger quest 2
    bot.current_step = "RR: Traveling to Foible's Fair"
    yield from Routines.Yield.Map.TravelToOutpost(FOIBLES_FAIR, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "RR: Exiting Foible's Fair"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_FOIBLE_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "RR: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "RR: Walking to ranger quest area"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_RANGER_SKILL_2, lambda: not bot.is_running)

    bot.current_step = "RR: Walking to quest NPC 2"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_RANGER_SKILL_3, lambda: not bot.is_running)

    bot.current_step = "RR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x805803)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x805801)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "RR: Closing dialogs"
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "RR: Returning to Ascalon"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Ranger skill quests complete"


def RunMonkSkillReq():
    """Monk skill unlock: Ascalon -> Abbey -> exit to pick up monk
    quests, then Barradin Estate area for final quest."""
    log = bot.log

    bot.current_step = "MR: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "MR: Traveling to Ashford Abbey"
    yield from Routines.Yield.Map.TravelToOutpost(ASHFORD_ABBEY, log)
    yield from Routines.Yield.wait(jitter(1000))

    # Exit Abbey -> first monk trainer
    bot.current_step = "MR: Walking to monk trainer 1"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_MONK_SKILL_1, lambda: Map.IsExplorable() or not bot.is_running)

    bot.current_step = "MR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x805703)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x805701)
    yield from Routines.Yield.wait(jitter(1500))

    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    # Second monk quest NPC
    bot.current_step = "MR: Interacting with monk NPC 2"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x804A03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x804A01)
    yield from Routines.Yield.wait(jitter(1500))

    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    # Travel to Ascalon -> exit -> Green Hills -> Barradin area for quest 3
    bot.current_step = "MR: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "MR: Abandoning Charr quest"
    Quest.AbandonQuest(CHARR_AT_THE_GATE)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "MR: Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "MR: Walking through Green Hills"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "MR: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "MR: Walking to monk quest NPC 3"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_MONK_SKILL_2, lambda: not bot.is_running)

    bot.current_step = "MR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x804D03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x804D01)
    yield from Routines.Yield.wait(jitter(1500))

    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "MR: Returning to Ascalon"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Monk skill quests complete"


def RunNecroSkillReq():
    """Necro skill unlock: Ascalon -> Abbey -> exit to pick up necro
    quests, then Barradin Estate area for final quest."""
    log = bot.log

    bot.current_step = "NR: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NR: Traveling to Ashford Abbey"
    yield from Routines.Yield.Map.TravelToOutpost(ASHFORD_ABBEY, log)
    yield from Routines.Yield.wait(jitter(1000))

    # Exit Abbey -> first necro trainer
    bot.current_step = "NR: Walking to necro trainer area"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_NECRO_SKILL_1, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "NR: Walking to necro NPC 1"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_NECRO_SKILL_2, lambda: not bot.is_running)

    bot.current_step = "NR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x804803)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x804801)
    yield from Routines.Yield.wait(jitter(1500))

    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    # Second necro quest NPC (same zone, different path)
    bot.current_step = "NR: Walking to necro NPC 2"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_NECRO_SKILL_3, lambda: not bot.is_running)

    bot.current_step = "NR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x802F03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x802F01)
    yield from Routines.Yield.wait(jitter(1500))

    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    # Travel to Ascalon -> exit -> Green Hills -> Barradin area for quest 3
    bot.current_step = "NR: Traveling to Ascalon City"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NR: Abandoning Charr quest"
    Quest.AbandonQuest(CHARR_AT_THE_GATE)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NR: Exiting Ascalon City"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_ABBEY_OUT, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "NR: Walking through Green Hills"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_BARRADIN_WALK_1, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable() and Party.IsPartyLoaded()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "NR: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NR: Walking to necro quest NPC 3"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_NECRO_SKILL_4, lambda: not bot.is_running)

    bot.current_step = "NR: Interacting with NPC"
    interact_quest_npc()
    yield from Routines.Yield.wait(jitter(1000))

    Player.SendDialog(0x802B03)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(0x802B01)
    yield from Routines.Yield.wait(jitter(1500))

    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))
    Keystroke.PressAndRelease(Key.Escape.value)
    yield from Routines.Yield.wait(jitter(1500))

    bot.current_step = "NR: Returning to Ascalon"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "Necro skill quests complete"


# ═══════════════════════════════════════════════════════════
# NICHOLAS SANDFORD (Yield coroutine)
# ═══════════════════════════════════════════════════════════

def RunNicholasSandford():
    """Travel to Fort Ranik, exit, walk to Nicholas Sandford,
    turn in collectible items for gifts."""
    log = bot.log

    bot.current_step = "NS: Switching district to Fort Ranik"
    Map.TravelToDistrict(FORT_RANIK, 6, 0)
    yield from Routines.Yield.wait(jitter(1000))
    while not (Map.IsMapReady() and is_outpost()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "NS: Exiting Fort Ranik"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_RANIK_EXIT, lambda: not bot.is_running)

    yield from Routines.Yield.wait(jitter(3000))
    while not (Map.IsMapReady() and Map.IsExplorable()):
        yield from Routines.Yield.wait(500)

    bot.current_step = "NS: Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NS: Walking to Nicholas Sandford"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_NICHOLAS_SANDFORD, lambda: not bot.is_running)

    bot.current_step = "NS: Targeting Nicholas"
    Keystroke.PressAndRelease(Key.V.value)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NS: Interacting with Nicholas"
    Keystroke.PressAndRelease(Key.Space.value)
    yield from Routines.Yield.wait(jitter(1000))

    bot.current_step = "NS: Accepting gift"
    Player.SendDialog(0x85)
    yield from Routines.Yield.wait(jitter(100))

    Player.SendDialog(0x86)
    yield from Routines.Yield.wait(jitter(100))

    bot.current_step = "Nicholas Sandford complete"


# ═══════════════════════════════════════════════════════════
# DISPATCHER ENTRIES (add to RunBotLogic() in v2)
# ═══════════════════════════════════════════════════════════

# Add these elif branches inside RunBotLogic() after the existing modes:
#
#   elif mode == 2:  # Charr Gate Opener
#       yield from RunCharrGateOpener()
#   elif mode == 3:  # Tame Pet
#       yield from RunTamePet()
#   elif mode == 4:  # Grand Tour
#       yield from RunGrandTour()
#   elif mode == 5:  # Nicholas Sandford
#       yield from RunNicholasSandford()
#   elif mode == 18: # Travel: Ashford Abbey
#       yield from TravelToAshfordAbbey()
#   elif mode == 19: # Travel: Foible's Fair
#       yield from TravelToFoiblesFair()
#   elif mode == 20: # Travel: Fort Ranik
#       yield from TravelToFortRanik()
#   elif mode == 21: # Travel: Barradin Estate
#       yield from TravelToBarradinEstate()
#   elif mode == 22: # Warrior Skill Quest
#       yield from RunWarriorSkillReq()
#   elif mode == 23: # Ranger Skill Quest
#       yield from RunRangerSkillReq()
#   elif mode == 24: # Monk Skill Quest
#       yield from RunMonkSkillReq()
#   elif mode == 25: # Necro Skill Quest
#       yield from RunNecroSkillReq()

# LEVEL 1 PROFESSION QUESTS
# ═══════════════════════════════════════════════════════════

def interact_nearest_npc(range_dist=800):
    """Find and interact with the nearest NPC."""
    my_xy = Player.GetXY()
    npc_array = AgentArray.GetNPCMinipetArray()
    npc_array = AgentArray.Filter.ByDistance(npc_array, my_xy, range_dist)
    npc_array = AgentArray.Sort.ByDistance(npc_array, my_xy)
    if npc_array:
        Player.Interact(npc_array[0])
        return True
    return False

def _run_profession_level1(trainer_path, reward_dialog, quest_take_dialog, quest_2_dialog,
                            quest_done_dialog, quest_fight_path, equip_model=WAND_MODEL,
                            monk_gwen=False, ele_loot=False, ranger_reequip=False):
    """Generic Level 1 profession quest coroutine. Handles all 6 professions via parameters."""
    log = bot.log

    # Bonus + equip
    bot.current_step = "Sending bonus command"
    Player.SendChatCommand("bonus")
    yield from Routines.Yield.wait(jitter(1000))
    bot.current_step = "Equipping items"
    Inventory.EquipItem(Item.GetItemIdFromModelID(equip_model))
    yield from Routines.Yield.wait(jitter(1000))
    Inventory.EquipItem(Item.GetItemIdFromModelID(SHIELD_MODEL))
    yield from Routines.Yield.wait(jitter(1000))

    # Town Crier quest
    bot.current_step = "Walking to Town Crier"
    yield from Routines.Yield.Movement.FollowPath(PATH_TO_TOWN_CRIER, lambda: not bot.is_running)
    interact_nearest_npc()
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(DIALOG['town_crier_quest'])
    yield from Routines.Yield.wait(jitter(500))

    # Sir Tydus reward
    bot.current_step = "Walking to Sir Tydus"
    yield from Routines.Yield.Movement.FollowPath(PATH_TO_SIR_TYDUS, lambda: not bot.is_running)
    interact_nearest_npc()
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(DIALOG['tydus_reward'])
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(reward_dialog)
    yield from Routines.Yield.wait(jitter(1500))

    # Exit Ascalon
    bot.current_step = "Exiting Ascalon"
    yield from Routines.Yield.Movement.FollowPath(
        PATH_EXIT_ASCALON, lambda: Map.IsExplorable() or not bot.is_running)
    yield from Routines.Yield.Map.WaitforMapLoad(0, log)
    yield from Routines.Yield.wait(jitter(1500))

    # Walk to trainer, take quest
    bot.current_step = "Walking to profession trainer"
    yield from Routines.Yield.Movement.FollowPath(trainer_path, lambda: not bot.is_running)
    interact_nearest_npc()
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(quest_take_dialog)
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(quest_2_dialog)
    yield from Routines.Yield.wait(jitter(1500))

    # Fire Imp + fight
    bot.current_step = "Using Fire Imp"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))

    if monk_gwen:
        # Monk special: walk to Gwen, interact, walk back
        bot.current_step = "Walking to find Gwen"
        yield from combat_loop(PATH_MONK_QUEST_1, exit_condition=lambda: not bot.is_running)
        interact_nearest_npc()
        yield from Routines.Yield.wait(jitter(1500))
        bot.current_step = "Returning to Ciglo"
        yield from combat_loop(PATH_MONK_QUEST_2, exit_condition=lambda: not bot.is_running)
        interact_nearest_npc()
        yield from Routines.Yield.wait(jitter(1500))
        Player.SendDialog(quest_done_dialog)
        yield from Routines.Yield.wait(jitter(1500))
    else:
        # Standard: fight along path, return, turn in
        bot.current_step = "Fighting quest enemies"
        yield from combat_loop(quest_fight_path, loot=ele_loot,
                               exit_condition=lambda: not bot.is_running)

        bot.current_step = "Returning to Ascalon"
        yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
        yield from Routines.Yield.wait(jitter(1500))
        yield from Routines.Yield.Movement.FollowPath(
            PATH_ASCALON_EXIT, lambda: Map.IsExplorable() or not bot.is_running)
        yield from Routines.Yield.Map.WaitforMapLoad(0, log)
        yield from Routines.Yield.wait(jitter(1500))

        bot.current_step = "Turning in quest"
        yield from Routines.Yield.Movement.FollowPath(trainer_path, lambda: not bot.is_running)
        interact_nearest_npc()
        yield from Routines.Yield.wait(jitter(1500))
        Player.SendDialog(quest_done_dialog)
        yield from Routines.Yield.wait(jitter(1500))

    # Althea skills
    bot.current_step = "Walking to Althea"
    yield from Routines.Yield.Movement.FollowPath(PATH_TO_ALTHEA, lambda: not bot.is_running)
    interact_nearest_npc()
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(DIALOG['althea_quest'])
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(DIALOG['althea_skills'])
    yield from Routines.Yield.wait(jitter(1500))

    # Leveling loop
    bot.current_step = "Using Fire Imp for leveling"
    Inventory.UseItem(Item.GetItemIdFromModelID(IMP_STONE))
    yield from Routines.Yield.wait(jitter(1000))
    bot.current_step = "Leveling combat loop"
    yield from combat_loop(PATH_LEVELING_LOOP, exit_condition=lambda: not bot.is_running)

    # Rurik quest
    bot.current_step = "Returning to Ascalon"
    yield from Routines.Yield.Map.TravelToOutpost(ASCALON_CITY, log)
    yield from Routines.Yield.wait(jitter(1500))
    bot.current_step = "Walking to Prince Rurik"
    yield from Routines.Yield.Movement.FollowPath(PATH_TO_RURIK, lambda: not bot.is_running)
    interact_nearest_npc()
    yield from Routines.Yield.wait(jitter(1500))
    Player.SendDialog(DIALOG['rurik_quest'])
    yield from Routines.Yield.wait(jitter(1500))

    if ranger_reequip:
        Inventory.EquipItem(Item.GetItemIdFromModelID(WAND_MODEL))
        yield from Routines.Yield.wait(jitter(1000))

def RunWarriorLevel1():
    yield from _run_profession_level1(
        PATH_TO_VAN, DIALOG['warrior_reward'], DIALOG['warrior_quest_take'],
        DIALOG['warrior_quest_2'], DIALOG['warrior_quest_done'], PATH_WARRIOR_QUEST)

def RunRangerLevel1():
    yield from _run_profession_level1(
        PATH_TO_ARTEMIS, DIALOG['ranger_reward'], DIALOG['ranger_quest_take'],
        DIALOG['ranger_quest_2'], DIALOG['ranger_quest_done'], PATH_RANGER_QUEST,
        equip_model=BOW_MODEL, ranger_reequip=True)

def RunMonkLevel1():
    yield from _run_profession_level1(
        PATH_TO_CIGLO, DIALOG['monk_reward'], DIALOG['monk_quest_take'],
        DIALOG['monk_quest_2'], DIALOG['monk_quest_done'], None, monk_gwen=True)

def RunNecromancerLevel1():
    yield from _run_profession_level1(
        PATH_TO_VERATA, DIALOG['necro_reward'], DIALOG['necro_quest_take'],
        DIALOG['necro_quest_2'], DIALOG['necro_quest_done'], PATH_NECRO_QUEST)

def RunMesmerLevel1():
    yield from _run_profession_level1(
        PATH_TO_SEBEDOH, DIALOG['mesmer_reward'], DIALOG['mesmer_quest_take'],
        DIALOG['mesmer_quest_2'], DIALOG['mesmer_quest_done'], PATH_MESMER_QUEST)

def RunElementalistLevel1():
    yield from _run_profession_level1(
        PATH_TO_HOWLAND, DIALOG['ele_reward'], DIALOG['ele_quest_take'],
        DIALOG['ele_quest_2'], DIALOG['ele_quest_done'], PATH_ELE_QUEST, ele_loot=True)

# ═══════════════════════════════════════════════════════════
# MAIN BOT LOGIC (coroutine dispatcher)
# ═══════════════════════════════════════════════════════════

def RunBotLogic():
    """Main coroutine: dispatches to the selected farm routine."""
    bot.stats.init_xp()
    bot.stats.global_timer.Start()

    while True:
        if not bot.is_running:
            yield from Routines.Yield.wait(500)
            continue

        mode = bot.selected_mode

        try:
            if mode == 0:    # Charr at the Gate (lvl 2-10)
                yield from RunCharrAtTheGate()
            elif mode == 1:  # Farmer Hamnet (lvl 11-20)
                yield from RunFarmerHamnet()
            elif mode == 7:  # Dull Carapaces
                yield from RunNickItemFarm([PATH_DULL_CARAPACES_1, PATH_DULL_CARAPACES_2])
            elif mode == 8:  # Gargoyle Skulls
                yield from RunNickItemFarm([PATH_GARGOYLE_SKULLS])
            elif mode == 9:  # Grawl Necklaces
                yield from RunNickItemFarm([PATH_GRAWL_NECKLACES])
            elif mode == 10: # Icy Lodestones
                yield from RunNickItemFarm([PATH_ICY_LODESTONES])
            elif mode == 11: # Enchanted Lodestones
                yield from RunNickItemFarm([PATH_ENCHANTED_LODE_GO, PATH_ENCHANTED_LODE],
                                           outpost=BARRADIN_ESTATE)
            elif mode == 12: # Red Iris Flowers
                yield from RunNickItemFarm([PATH_RED_IRIS_1, PATH_RED_IRIS_2, PATH_RED_IRIS_3,
                                            PATH_RED_IRIS_4, PATH_RED_IRIS_5], aggro_only=True)
            elif mode == 13: # Skeletal Limbs
                yield from RunNickItemFarm([PATH_GREENHILLS_TO_CAT, PATH_SKELE_LIMBS],
                                           district_switch=True, district_outpost=BARRADIN_ESTATE)
            elif mode == 14: # Skale Fins
                yield from RunNickItemFarm([PATH_SKALE_FINS],
                                           district_switch=True, district_outpost=FORT_RANIK)
            elif mode == 15: # Spider Legs
                yield from RunNickItemFarm([PATH_SPIDER_LEGS_1, PATH_SPIDER_LEGS_2],
                                           district_switch=True, district_outpost=FORT_RANIK)
            elif mode == 16: # Unnatural Seeds
                yield from RunNickItemFarm([PATH_UNNATURAL_SEEDS])
            elif mode == 17: # Worn Belts
                yield from RunNickItemFarm([PATH_WORN_BELTS])
            elif mode == 6:  # Baked Husks
                yield from RunNickItemFarm([PATH_BAKED_HUSKS], outpost=ASHFORD_ABBEY)
            elif mode == 20: yield from RunWarriorLevel1()
            elif mode == 21: yield from RunRangerLevel1()
            elif mode == 22: yield from RunMonkLevel1()
            elif mode == 23: yield from RunNecromancerLevel1()
            elif mode == 24: yield from RunMesmerLevel1()
            elif mode == 25: yield from RunElementalistLevel1()
            # Utility routines
            elif mode == 2:  yield from TravelToAshfordAbbey()
            elif mode == 3:  yield from TravelToFoiblesFair()
            elif mode == 4:  yield from TravelToFortRanik()
            elif mode == 5:  yield from TravelToBarradinEstate()
            elif mode == 19: yield from RunGrandTour()
            elif mode == 26: yield from RunNicholasSandford()
            elif mode == 27: yield from RunTamePet()
            elif mode == 28: yield from RunWarriorSkillReq()
            elif mode == 30: yield from RunRangerSkillReq()
            elif mode == 32: yield from RunMonkSkillReq()
            elif mode == 34: yield from RunNecroSkillReq()
            elif mode == 42: yield from RunCharrGateOpener()
            else:
                yield from Routines.Yield.wait(1000)
        except Exception as e:
            ConsoleLog(MODULE_NAME, f"Error in farm routine: {e}", Py4GW.Console.MessageType.Error, log=True)
            bot.is_running = False

# ═══════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════

XP_TABLE = {
    1: 2000, 2: 4600, 3: 7800, 4: 11600, 5: 16000,
    6: 21000, 7: 26600, 8: 32800, 9: 39600, 10: 47000,
    11: 55000, 12: 63600, 13: 72800, 14: 82600, 15: 93000,
    16: 104000, 17: 115600, 18: 127800, 19: 140600, 20: 140600
}

def DrawWindow():
    try:
        if bot.window_module.first_run:
            PyImGui.set_next_window_size(*bot.window_module.window_size)
            PyImGui.set_next_window_pos(*bot.window_module.window_pos)
            bot.window_module.first_run = False

        if PyImGui.begin("\uf647  TH3KUM1KO'S PRESEARING BIBLE", bot.window_module.window_flags):

            if PyImGui.begin_tab_bar("MainTabBar"):

                # ── LEVELING TAB ──────────────────────────────────────
                if PyImGui.begin_tab_item("LDoA"):
                    PyImGui.spacing()
                    PyImGui.text_wrapped("WITH THESE FUNCTIONS, THE BOT WILL LEVEL UP YOUR CHARACTER BASED ON ITS PROFESSION, REACHING LEVEL 2 THROUGH THE INITIAL QUESTS. ONCE LEVEL 2 IS ACHIEVED, THE BOT WILL AUTOMATICALLY TAKE THE 'CHARR AT THE GATE' QUEST.")
                    bot.selected_mode = PyImGui.radio_button(" \uf132 WARRIOR", bot.selected_mode, 20)
                    PyImGui.same_line(200, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf54c NECROMANCER", bot.selected_mode, 23)
                    bot.selected_mode = PyImGui.radio_button(" \uf1bb RANGER", bot.selected_mode, 21)
                    PyImGui.same_line(200, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \ue2ca MESMER", bot.selected_mode, 24)
                    bot.selected_mode = PyImGui.radio_button(" \uf644 MONK", bot.selected_mode, 22)
                    PyImGui.same_line(200, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf6e8 ELEMENTALIST", bot.selected_mode, 25)
                    PyImGui.spacing()
                    PyImGui.text_wrapped("WITH THIS FUNCTION, THE BOT WILL LEAVE ASCALON USING THE 'CHARR AT THE GATE' QUEST, FOLLOW RURIK, AND WAIT FOR THE PRINCE TO KILL THREE CHARR BEFORE RETURNING TO THE CITY AND RESTARTING THE LOOP. THIS FUNCTION ENSURES SURVIVOR TITLE PROGRESSION.")
                    bot.selected_mode = PyImGui.radio_button(" \uf443 LEVEL 2-10 - CHARR AT THE GATE", bot.selected_mode, 0)
                    PyImGui.spacing()
                    PyImGui.text_wrapped("ONCE YOU REACH LEVEL 10, WAIT FOR THE ROTATION OF THE VANGUARD QUESTS UNTIL THE 'FARMER HAMNET' QUEST BECOMES AVAILABLE. THE BOT WILL KILL THE FIRST TWO MOBS OUTSIDE FOIBLE'S FAIR AND REPEAT THE LOOP UNTIL LEVEL 20. THIS FUNCTION ENSURES SURVIVOR TITLE PROGRESSION.")
                    bot.selected_mode = PyImGui.radio_button(" \uf43f LEVEL 11-20 - FARMER HAMNET", bot.selected_mode, 1)
                    PyImGui.spacing()

                    if bot.is_running:
                        if PyImGui.button(" \uf04d   STOP##leveling"):
                            bot.is_running = False
                            bot.current_step = "Idle"
                    else:
                        if PyImGui.button(" \uf04b   START##leveling"):
                            bot.is_running = True
                            bot.stats.init_xp()
                            GLOBAL_CACHE.Coroutines.clear()
                            GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                            bot.stats.global_timer.Start()

                    PyImGui.end_tab_item()

                # ── NICK ITEMS TAB ────────────────────────────────────
                if PyImGui.begin_tab_item("NIC ITEMS"):
                    PyImGui.spacing()
                    bot.selected_mode = PyImGui.radio_button("\ue599 BAKED HUSKS", bot.selected_mode, 6)
                    bot.selected_mode = PyImGui.radio_button("\uf188 DULL CARAPACES", bot.selected_mode, 8)
                    bot.selected_mode = PyImGui.radio_button("\uf54c GARGOYLE SKULLS", bot.selected_mode, 9)
                    bot.selected_mode = PyImGui.radio_button("\uf4d6 GRAWL NECKLACES", bot.selected_mode, 10)
                    bot.selected_mode = PyImGui.radio_button("\uf7ad ICY LODESTONES", bot.selected_mode, 11)
                    bot.selected_mode = PyImGui.radio_button("\uf3a5 ENCHANTED LODESTONES", bot.selected_mode, 12)
                    bot.selected_mode = PyImGui.radio_button("\uf5bb RED IRIS FLOWERS", bot.selected_mode, 13)
                    bot.selected_mode = PyImGui.radio_button("\uf5d7 SKELETAL LIMBS", bot.selected_mode, 14)
                    bot.selected_mode = PyImGui.radio_button("\ue4f2 SKALE FINS", bot.selected_mode, 15)
                    bot.selected_mode = PyImGui.radio_button("\uf717 SPIDER LEGS", bot.selected_mode, 16)
                    bot.selected_mode = PyImGui.radio_button("\uf4d8 UNNATURAL SEEDS", bot.selected_mode, 17)
                    bot.selected_mode = PyImGui.radio_button("\ue19b WORN BELTS", bot.selected_mode, 18)
                    bot.selected_mode = PyImGui.radio_button("\uf06b NICHOLAS SANDFORD", bot.selected_mode, 26)
                    PyImGui.spacing()

                    if bot.is_running:
                        if PyImGui.button(" \uf04d   STOP##nick"):
                            bot.is_running = False
                            bot.current_step = "Idle"
                    else:
                        if PyImGui.button(" \uf04b   START##nick"):
                            bot.is_running = True
                            bot.stats.init_xp()
                            GLOBAL_CACHE.Coroutines.clear()
                            GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                            bot.stats.global_timer.Start()

                    PyImGui.end_tab_item()

                # ── MISC TAB ──────────────────────────────────────────
                if PyImGui.begin_tab_item("MISC"):
                    bot.selected_mode = PyImGui.radio_button("\uf6be TAME PET", bot.selected_mode, 27)
                    bot.selected_mode = PyImGui.radio_button("\uf70c CHARR GATE OPENER", bot.selected_mode, 42)

                    if bot.is_running:
                        if PyImGui.button(" \uf04d   STOP##misc"):
                            bot.is_running = False
                            bot.current_step = "Idle"
                    else:
                        if PyImGui.button(" \uf04b   START##misc"):
                            bot.is_running = True
                            bot.stats.init_xp()
                            GLOBAL_CACHE.Coroutines.clear()
                            GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                            bot.stats.global_timer.Start()

                    PyImGui.end_tab_item()

                # ── TRAVEL TAB ────────────────────────────────────────
                if PyImGui.begin_tab_item("TRAVEL"):
                    bot.selected_mode = PyImGui.radio_button("\uf51d GO TO ASHFORD ABBEY", bot.selected_mode, 2)
                    bot.selected_mode = PyImGui.radio_button("\uf6e8 GO TO FOIBLE'S FAIR", bot.selected_mode, 3)
                    bot.selected_mode = PyImGui.radio_button("\uf447 GO TO FORT RANIK", bot.selected_mode, 4)
                    bot.selected_mode = PyImGui.radio_button(" \uf43a GO TO THE BARRADIN ESTATE", bot.selected_mode, 5)
                    bot.selected_mode = PyImGui.radio_button("\uf5a0 THE GRAND TOUR", bot.selected_mode, 19)

                    if bot.is_running:
                        if PyImGui.button(" \uf04d   STOP##travel"):
                            bot.is_running = False
                            bot.current_step = "Idle"
                    else:
                        if PyImGui.button(" \uf04b   START##travel"):
                            bot.is_running = True
                            bot.stats.init_xp()
                            GLOBAL_CACHE.Coroutines.clear()
                            GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                            bot.stats.global_timer.Start()

                    PyImGui.end_tab_item()

                # ── SKILLS TAB ────────────────────────────────────────
                if PyImGui.begin_tab_item("SKILLS"):
                    PyImGui.spacing()
                    PyImGui.text_colored("\uf132 UNLOCK WARRIOR SKILL", (1.0, 1.0, 0.2, 1.0))
                    PyImGui.pop_style_color(1)
                    PyImGui.same_line(300, -1.0)
                    PyImGui.text_colored("\uf54c UNLOCK NECROMANCER SKILL", (0.0, 0.8, 0.3, 1.0))
                    PyImGui.pop_style_color(1)

                    bot.selected_mode = PyImGui.radio_button(" \uf00c PRIMARY/SECONDARY REQUIRED##WARRIOR_REQ", bot.selected_mode, 28)
                    PyImGui.same_line(300, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf00c PRIMARY/SECONDARY REQUIRED##NECRO_REQ", bot.selected_mode, 34)
                    bot.selected_mode = PyImGui.radio_button(" \uf00d PRIMARY/SECONDARY NO REQ##WARRIOR_NOREQ", bot.selected_mode, 29)
                    PyImGui.same_line(300, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf00d PRIMARY/SECONDARY NO REQ##NECRO_NOREQ", bot.selected_mode, 35)

                    PyImGui.text_colored("\uf1bb UNLOCK RANGER SKILL", (1.0, 0.5, 0.0, 1.0))
                    PyImGui.pop_style_color(1)
                    PyImGui.same_line(300, -1.0)
                    PyImGui.text_colored("\ue2ca UNLOCK MESMER SKILL", (1.0, 0.1, 0.8, 1.0))
                    PyImGui.pop_style_color(1)

                    bot.selected_mode = PyImGui.radio_button(" \uf00c PRIMARY/SECONDARY REQUIRED##RANGER_REQ", bot.selected_mode, 30)
                    PyImGui.same_line(300, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf00c PRIMARY/SECONDARY REQUIRED##MESMER_REQ", bot.selected_mode, 36)
                    bot.selected_mode = PyImGui.radio_button(" \uf00d PRIMARY/SECONDARY NO REQ##RANGER_NOREQ", bot.selected_mode, 31)
                    PyImGui.same_line(300, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf00d PRIMARY/SECONDARY NO REQ##MESMER_NOREQ", bot.selected_mode, 37)

                    PyImGui.text_colored("\uf644 UNLOCK MONK SKILL", (0.2, 1.0, 1.0, 1.0))
                    PyImGui.pop_style_color(1)
                    PyImGui.same_line(300, -1.0)
                    PyImGui.text_colored("\uf6e8 UNLOCK ELEMENTALIST SKILL", (1.0, 0.0, 0.2, 1.0))
                    PyImGui.pop_style_color(1)

                    bot.selected_mode = PyImGui.radio_button(" \uf00c PRIMARY/SECONDARY REQUIRED##MONK_REQ", bot.selected_mode, 32)
                    PyImGui.same_line(300, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf00c PRIMARY/SECONDARY REQUIRED##ELEMENTALIST_REQ", bot.selected_mode, 38)
                    bot.selected_mode = PyImGui.radio_button(" \uf00d PRIMARY/SECONDARY NO REQ##MONK_NOREQ", bot.selected_mode, 33)
                    PyImGui.same_line(300, -1.0)
                    bot.selected_mode = PyImGui.radio_button(" \uf00d PRIMARY/SECONDARY NO REQ##ELEMENTALIST_NOREQ", bot.selected_mode, 39)
                    PyImGui.spacing()

                    if bot.is_running:
                        if PyImGui.button(" \uf04d   STOP##skills"):
                            bot.is_running = False
                            bot.current_step = "Idle"
                    else:
                        if PyImGui.button(" \uf04b   START##skills"):
                            bot.is_running = True
                            bot.stats.init_xp()
                            GLOBAL_CACHE.Coroutines.clear()
                            GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                            bot.stats.global_timer.Start()

                    PyImGui.end_tab_item()

                # ── STATS TAB ─────────────────────────────────────────
                if PyImGui.begin_tab_item("STATS"):
                    level = Agent.GetLevel(Player.GetAgentID())
                    xp = Player.GetExperience()
                    xp_req = XP_TABLE.get(level, 140600)
                    xp_pct = min(100, int(xp / xp_req * 100)) if xp_req > 0 else 0
                    xp_gained = bot.stats.get_xp_gained()
                    xp_hr = bot.stats.get_xp_per_hour()
                    elapsed = bot.stats.global_timer.GetElapsedTime() / 1000.0
                    elapsed_fmt = time.strftime("%H:%M:%S", time.gmtime(elapsed))

                    avg_run_time = elapsed / bot.stats.runs if bot.stats.runs > 0 else 0
                    avg_run_fmt = time.strftime("%H:%M:%S", time.gmtime(avg_run_time))

                    xp_to_next = max(0, xp_req - xp)
                    ttl = time.strftime("%H:%M:%S", time.gmtime(xp_to_next / xp_hr * 3600)) if xp_hr > 0 else "--:--:--"

                    headers = ["INFO", "DATA"]
                    data = [
                        ("TIMER", elapsed_fmt),
                        ("RUN COUNTER", str(bot.stats.runs)),
                        ("AVG RUN TIME", avg_run_fmt),
                        ("LEVEL", f"{level}/20"),
                        ("EXPERIENCE", f"{xp}/{xp_req} ({xp_pct}%)"),
                        ("XP GAINED", f"+{xp_gained:,}"),
                        ("XP / HOUR", f"{xp_hr:,}"),
                        ("TIME TO NEXT LVL", ttl),
                        ("FREE SLOTS", str(Inventory.GetFreeSlotCount())),
                    ]
                    ImGui.table("PLAYER INFO", headers, data)
                    PyImGui.spacing()
                    PyImGui.end_tab_item()

                # ── INVENTORY TAB ─────────────────────────────────────
                if PyImGui.begin_tab_item("INVENTORY"):
                    headers = ["ITEM NAME", "FARMED / (COUNT)"]
                    data = []
                    for model_id, name in bot.tracked_items.items():
                        try:
                            count = Inventory.GetModelCount(model_id)
                            initial = bot.initial_quantities.get(model_id, 0)
                            farmed = max(0, count - initial)
                            farmed_text = f"+{farmed}" if farmed > 0 else "0"
                            count_text = str(count) if count > 0 else "0"
                            data.append((name, f"{farmed_text} / ({count_text})"))
                        except Exception:
                            data.append((name, "? / (?)"))
                    ImGui.table("INVENTORY", headers, data)

                    PyImGui.spacing()
                    PyImGui.text("Salvage Settings:")
                    bot.inventory.salvage_whites = PyImGui.checkbox("Salvage Whites", bot.inventory.salvage_whites)
                    bot.inventory.salvage_blues = PyImGui.checkbox("Salvage Blues", bot.inventory.salvage_blues)
                    PyImGui.spacing()
                    PyImGui.end_tab_item()

                PyImGui.end_tab_bar()

            PyImGui.end()

    except Exception as e:
        ConsoleLog(MODULE_NAME, f"UI Error: {e}", Py4GW.Console.MessageType.Error, log=True)

# ═══════════════════════════════════════════════════════════
# MAIN (per-frame entry point)
# ═══════════════════════════════════════════════════════════

def main():
    try:
        if Map.IsMapLoading():
            return

        if not Map.IsMapReady() or Player.GetAgentID() <= 0:
            return

        DrawWindow()

        if bot.is_running:
            # Death recovery
            if Map.IsExplorable() and is_dead():
                ConsoleLog(MODULE_NAME, "Death detected - resetting", Py4GW.Console.MessageType.Warning)
                bot.stats.deaths += 1
                GLOBAL_CACHE.Coroutines.clear()
                GLOBAL_CACHE.Coroutines.append(RunBotLogic())
                return

            # Step coroutines (only when not casting/knocked down)
            if not Agent.IsCasting(Player.GetAgentID()) and not Agent.IsKnockedDown(Player.GetAgentID()):
                for coro in GLOBAL_CACHE.Coroutines[:]:
                    try:
                        next(coro)
                    except StopIteration:
                        GLOBAL_CACHE.Coroutines.remove(coro)

    except Exception as e:
        ConsoleLog(MODULE_NAME, f"Error: {e}", Py4GW.Console.MessageType.Error, log=True)

if __name__ == "__main__":
    main()
