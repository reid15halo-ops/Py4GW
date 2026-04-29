"""GW1 item modifier constants and valuable item tables.

Pure-data module containing:
- MOD_* constants: numeric identifiers for the Guild Wars 1 item modifier system.
- VALUABLE_* dicts/sets: threshold tables used by AutoLootManager to decide
  which items are worth keeping, salvaging, or extracting mods from.

This module has no imports and no side effects.
"""

# ---------------------------------------------------------------------------
#  Modifier Constants
# ---------------------------------------------------------------------------
# Key modifier identifiers from the GW item system
MOD_REQUIREMENT     = 10136   # arg1=attribute, arg2=req level
MOD_REQUIREMENT_2   = 32784   # alternate req modifier (same format)
MOD_DAMAGE_TYPE     = 9400    # arg1=damage type
MOD_DAMAGE_RANGE    = 42920   # arg1=max dmg, arg2=min dmg
MOD_HEALTH_FLAT     = 9032    # arg1=HP value (Fortitude: +30)
MOD_HEALTH_ENCHANT  = 9064    # arg1=HP (Devotion: while enchanted)
MOD_HEALTH_HEXED    = 9080    # arg1=HP (while hexed)
MOD_HEALTH_STANCE   = 9096    # Health while in stance
MOD_HEALTH_RUNE     = 8408    # arg2=HP (+/- from rune/inscription, Vigor)
MOD_ENERGY_FLAT     = 8920    # arg2=energy (Insightful: +5)
MOD_ENERGY_ENCHANT  = 8952    # arg2=energy (while enchanted)
MOD_ENERGY_HEXED    = 9000    # arg2=energy (while hexed)
MOD_ENERGY_OFFHAND  = 26568   # arg1=energy ("of the [class]" on focus/offhand)
MOD_ENCHANTING      = 8888    # arg2=% enchant duration (of Enchanting: 20%)
MOD_VAMPIRIC        = 8424    # life steal (health degen)
MOD_ZEALOUS         = 9240    # energy on hit
MOD_SUNDERING       = 9208    # armor penetration %
MOD_FURIOUS         = 9144    # double adrenaline %
MOD_ARMOR_FLAT      = 8456    # arg2=armor (of Defense / "of the [class]" on shields)
MOD_ARMOR_ENCHANT   = 8600    # armor while enchanted
MOD_ARMOR_HEXED     = 8648    # armor while hexed
MOD_ARMOR_ATTACK    = 8568    # armor while attacking (Might Makes Right)
MOD_ARMOR_CAST      = 8584    # armor while casting (Knowing is Half the Battle)
MOD_ARMOR_ABOVE_HP  = 8616    # armor while HP above X
MOD_ARMOR_BELOW_HP  = 8632    # armor while HP below X
MOD_WARDING         = 8488    # armor vs elemental
MOD_SHELTER         = 8536    # armor vs physical
MOD_DMG_PERCENT     = 8760    # arg2=dmg% (flat)
MOD_DMG_ENCHANT     = 8808    # dmg% while enchanted (Guided by Fate)
MOD_DMG_ABOVE_HP    = 8824    # dmg% while HP above X (Strength and Honor 15^50)
MOD_DMG_BELOW_HP    = 8840    # dmg% while HP below X
MOD_DMG_HEXED       = 8792    # dmg% vs hexed (Too Much Information)
MOD_DMG_HEXED2      = 8856    # dmg% while hexed (Don't Fear the Reaper)
MOD_DMG_STANCE      = 8872    # dmg% in stance (Dance With Death)
MOD_PHYS_REDUCE     = 8312    # received phys dmg -%  (Luck of the Draw)
MOD_PHYS_ENCHANT    = 8328    # received phys dmg -% while enchanted (Sheltered by Faith)
MOD_PHYS_HEXED      = 8344    # received phys dmg -% while hexed
MOD_PHYS_STANCE     = 8360    # received phys dmg -% in stance
MOD_HCT_ATTR        = 8712    # Halves casting time % (Don't Think Twice)
MOD_HSR_ATTR         = 9112   # Halves skill recharge of [attr] %
MOD_HSR_ALL          = 9128   # Halves skill recharge of all spells %
MOD_HSR_GENERIC      = 10280  # Halves skill recharge % (generic)
MOD_REDUCE_COND      = 10328  # reduces condition duration
MOD_REDUCE_CRIPPLE   = 9336   # reduce cripple
MOD_REDUCE_BLIND     = 9320   # reduce blind
MOD_INSCRIPTION      = 42288  # inscription slot identifier
MOD_INSCRIPTION_NAME = 42290  # inscription name/type
MOD_ATTR_BONUS       = 10296  # +1 attribute (Master of My Domain / Aptitude)
MOD_DEATHBANE        = 41544  # dmg vs undead
MOD_ENERGY_REGEN     = 8392   # energy regen/degen
MOD_ENERGY_MINUS     = 8376   # energy -X (Brawn over Brains)

# ---------------------------------------------------------------------------
#  Valuable Mod Tables
# ---------------------------------------------------------------------------

# === WEAPONS (swords, axes, hammers, bows, daggers, scythes, spears, wands, staves) ===
# Format: modifier_id -> (arg_to_check, min_value_to_keep, description)
#
# Two tiers of thresholds based on GW1 trading economy:
#   PURPLE: near-max rolls worth extracting (purples have two mods, higher chance
#           of a good combo, and near-max is still tradeable).
#   BLUE:   only PERFECT (max) rolls worth the expert kit cost.  Blues have a
#           single mod so only the absolute best roll justifies extraction.
#           Vampiric/Zealous on blues are too common and cheap to bother.

VALUABLE_WEAPON_MODS_PURPLE = {
    MOD_HEALTH_FLAT:    ("arg1", 28, "HP +{v}"),           # Fortitude +28-30
    MOD_ENERGY_FLAT:    ("arg2", 5, "Energy +{v}"),        # +5 energy (Insightful)
    MOD_ENCHANTING:     ("arg2", 19, "Enchanting +{v}%"),  # 19-20% enchanting duration
    MOD_SUNDERING:      ("arg2", 19, "Sundering {v}%"),    # 19-20% armor pen
    MOD_FURIOUS:        ("arg2", 9, "Furious {v}%"),       # 9-10% double adrenaline
    MOD_ZEALOUS:        ("arg2", 1, "Zealous"),            # worth extracting on purple (has 2nd mod)
    MOD_VAMPIRIC:       ("arg1", 1, "Vampiric"),           # worth extracting on purple (has 2nd mod)
    MOD_DMG_ABOVE_HP:   ("arg2", 14, "15^50 +{v}%"),      # Strength and Honor
    MOD_DMG_ENCHANT:    ("arg2", 14, "Guided +{v}%"),      # Guided by Fate
    MOD_DMG_STANCE:     ("arg2", 14, "Stance +{v}%"),      # Dance With Death
    MOD_HCT_ATTR:       ("arg1", 19, "HCT {v}%"),         # 19-20% halves casting
    MOD_HSR_ALL:        ("arg1", 19, "HSR {v}%"),          # 19-20% halves recharge
    MOD_HSR_ATTR:       ("arg1", 19, "HSR(attr) {v}%"),    # 19-20% attr-specific
    MOD_ATTR_BONUS:     ("arg1", 19, "+1 attr {v}%"),      # 19-20% +1 attribute
}

VALUABLE_WEAPON_MODS_BLUE = {
    # Blues: only PERFECT (max) rolls — the single mod must be absolute best value.
    # Vampiric/Zealous intentionally excluded: too common on blues, not worth the kit.
    MOD_HEALTH_FLAT:    ("arg1", 30, "HP +{v}"),           # Only +30 (max Fortitude)
    MOD_ENERGY_FLAT:    ("arg2", 5, "Energy +{v}"),        # +5 energy (always max)
    MOD_ENCHANTING:     ("arg2", 20, "Enchanting +{v}%"),  # Only 20% (max)
    MOD_SUNDERING:      ("arg2", 20, "Sundering {v}%"),    # Only 20% (max)
    MOD_FURIOUS:        ("arg2", 10, "Furious {v}%"),      # Only 10% (max)
    MOD_DMG_ABOVE_HP:   ("arg2", 15, "15^50 +{v}%"),      # Only 15% (max)
    MOD_DMG_ENCHANT:    ("arg2", 15, "Guided +{v}%"),      # Only 15% (max)
    MOD_DMG_STANCE:     ("arg2", 15, "Stance +{v}%"),      # Only 15% (max)
    MOD_HCT_ATTR:       ("arg1", 20, "HCT {v}%"),         # Only 20% (max)
    MOD_HSR_ALL:        ("arg1", 20, "HSR {v}%"),          # Only 20% (max)
    MOD_HSR_ATTR:       ("arg1", 20, "HSR(attr) {v}%"),    # Only 20% (max)
    MOD_ATTR_BONUS:     ("arg1", 20, "+1 attr {v}%"),      # Only 20% (max)
}

# Backward-compat alias for any external callers
VALUABLE_WEAPON_MODS = VALUABLE_WEAPON_MODS_PURPLE

# === SHIELDS & OFFHANDS (focus items) ===
# Shield "of the [class]": only +8 armor (max) is worth extracting on any rarity.
# Focus "of the [class]": +5 energy is always the roll, valuable at any rarity.
# "of Shelter" (+armor vs physical) and "of Warding" (+armor vs elemental) are
# high-value shield mods — only near-max (+9/+10) justifies extraction.

VALUABLE_SHIELD_OFFHAND_MODS_PURPLE = {
    MOD_HEALTH_FLAT:    ("arg1", 28, "HP +{v}"),           # Fortitude +28-30
    MOD_HEALTH_ENCHANT: ("arg1", 28, "Devotion +{v}"),     # +28-30 while enchanted
    MOD_HEALTH_HEXED:   ("arg1", 28, "HP hexed +{v}"),     # +28-30 while hexed
    MOD_HEALTH_STANCE:  ("arg1", 28, "HP stance +{v}"),    # +28-30 in stance
    MOD_ARMOR_FLAT:     ("arg2", 8, "Armor +{v}"),         # Only +8 armor ("of the [class]" shield max)
    MOD_ARMOR_ENCHANT:  ("arg2", 7, "Armor ench +{v}"),    # +7-8 while enchanted
    MOD_ARMOR_HEXED:    ("arg2", 7, "Armor hex +{v}"),     # +7-8 while hexed
    MOD_ARMOR_ATTACK:   ("arg2", 7, "Armor atk +{v}"),     # +7-8 while attacking
    MOD_ARMOR_CAST:     ("arg2", 7, "Armor cast +{v}"),    # +7-8 while casting
    MOD_ARMOR_ABOVE_HP: ("arg2", 7, "Armor >HP +{v}"),     # +7-8 HP above X
    MOD_WARDING:        ("arg2", 9, "Warding +{v}"),       # +9-10 vs elemental (valuable)
    MOD_SHELTER:        ("arg2", 9, "Shelter +{v}"),        # +9-10 vs physical (valuable)
    MOD_REDUCE_COND:    ("arg2", 1, "Reduce cond"),         # any condition reduce
    MOD_REDUCE_BLIND:   ("arg1", 1, "Reduce blind"),
    MOD_REDUCE_CRIPPLE: ("arg1", 1, "Reduce cripple"),
    MOD_ENERGY_FLAT:    ("arg2", 5, "Energy +{v}"),         # +5 energy
    MOD_ENERGY_ENCHANT: ("arg2", 5, "Energy ench +{v}"),    # +5 while enchanted
    MOD_ENERGY_OFFHAND: ("arg1", 5, "Energy class +{v}"),   # "of the [class]" +5 energy on focus
    MOD_HSR_ALL:        ("arg1", 19, "HSR {v}%"),
    MOD_HSR_ATTR:       ("arg1", 19, "HSR(attr) {v}%"),
    MOD_HCT_ATTR:       ("arg1", 19, "HCT {v}%"),
}

VALUABLE_SHIELD_OFFHAND_MODS_BLUE = {
    # Blues: only PERFECT (max) rolls justify expert kit cost
    MOD_HEALTH_FLAT:    ("arg1", 30, "HP +{v}"),           # Only +30 (max Fortitude)
    MOD_HEALTH_ENCHANT: ("arg1", 30, "Devotion +{v}"),     # Only +30 while enchanted
    MOD_HEALTH_HEXED:   ("arg1", 30, "HP hexed +{v}"),     # Only +30 while hexed
    MOD_HEALTH_STANCE:  ("arg1", 30, "HP stance +{v}"),    # Only +30 in stance
    MOD_ARMOR_FLAT:     ("arg2", 8, "Armor +{v}"),         # Only +8 ("of the [class]" shield max)
    MOD_ARMOR_ENCHANT:  ("arg2", 8, "Armor ench +{v}"),    # Only +8 while enchanted
    MOD_ARMOR_HEXED:    ("arg2", 8, "Armor hex +{v}"),     # Only +8 while hexed
    MOD_ARMOR_ATTACK:   ("arg2", 8, "Armor atk +{v}"),     # Only +8 while attacking
    MOD_ARMOR_CAST:     ("arg2", 8, "Armor cast +{v}"),    # Only +8 while casting
    MOD_ARMOR_ABOVE_HP: ("arg2", 8, "Armor >HP +{v}"),     # Only +8 HP above X
    MOD_WARDING:        ("arg2", 10, "Warding +{v}"),      # Only +10 vs elemental (max)
    MOD_SHELTER:        ("arg2", 10, "Shelter +{v}"),       # Only +10 vs physical (max)
    # No reduce-condition on blue — too common, not worth expert kit
    MOD_ENERGY_FLAT:    ("arg2", 5, "Energy +{v}"),         # +5 energy (always max)
    MOD_ENERGY_ENCHANT: ("arg2", 5, "Energy ench +{v}"),    # +5 while enchanted
    MOD_ENERGY_OFFHAND: ("arg1", 5, "Energy class +{v}"),   # "of the [class]" +5 energy on focus
    MOD_HSR_ALL:        ("arg1", 20, "HSR {v}%"),           # Only 20% (max)
    MOD_HSR_ATTR:       ("arg1", 20, "HSR(attr) {v}%"),     # Only 20% (max)
    MOD_HCT_ATTR:       ("arg1", 20, "HCT {v}%"),          # Only 20% (max)
}

# Backward-compat alias
VALUABLE_SHIELD_OFFHAND_MODS = VALUABLE_SHIELD_OFFHAND_MODS_PURPLE

# === ARMOR PIECES (runes, insignias) ===
# These determine if we should EXPERT SALVAGE to extract the rune/insignia
VALUABLE_ARMOR_RUNES = {
    MOD_HEALTH_RUNE:    ("arg2", 41, "Vigor +{v}HP"),      # Major Vigor=41, Superior=50
}

# Name-based rune/insignia detection (checked after identification)
VALUABLE_RUNE_KEYWORDS = {
    # Always valuable runes
    "superior vigor", "major vigor",
    "survivor", "radiant", "attunement",
    # Valuable profession-specific superior runes
    "superior fire magic", "superior death magic",
    "superior protection prayers", "superior healing prayers",
    "superior domination magic", "superior illusion magic",
    "superior inspiration magic", "superior channeling magic",
    "superior dagger mastery", "superior critical strikes",
    "superior scythe mastery", "superior mysticism",
    "superior command", "superior leadership",
    "superior marksmanship", "superior expertise",
    "superior soul reaping", "superior curses",
    "superior blood magic", "superior smiting prayers",
    "superior divine favor", "superior fast casting",
    "superior spawning power", "superior communing",
    "superior restoration magic", "superior wilderness survival",
    "superior beast mastery", "superior axe mastery",
    "superior swordsmanship", "superior hammer mastery",
    "superior tactics", "superior strength",
    "superior earth prayers", "superior wind prayers",
}

# Valuable insignia keywords (always extract)
VALUABLE_INSIGNIA_KEYWORDS = {
    "survivor", "radiant", "attunement",
    "blessed", "herald's", "centurion's",
    "sentinel's", "knight's", "wanderer's",
}

# === VALUABLE SKINS (always keep entire item, never salvage) ===
VALUABLE_MODEL_IDS = {
    # High-value weapon skins (model IDs) — always KEEP, never salvage
    # Swords
    2460,   # Crystalline Sword
    1932,   # Eternal Blade
    1640,   # Emerald Blade
    36,     # Katana
    # Axes
    397,    # Chaos Axe
    370,    # Colossal Pick
    # Staves
    2127,   # Bone Dragon Staff
    408,    # Plagueborn Staff
    # Shields
    2133,   # Amethyst Aegis
    2135,   # Demonic Aegis
    2134,   # Draconic Aegis
    # Spears/Scythes
    2475,   # Voltaic Spear
    # Festival/event
    2509,   # Straw Effigy
    2510,   # Paper Fan
    29,     # Celestial Compass
}

VALUABLE_SKIN_KEYWORDS = {
    "eternal", "voltaic", "crystalline", "celestial", "obsidian",
    "zodiac", "demonic", "draconic", "dhuum", "bone dragon",
    "chaos axe", "eaglecrest", "silverwing", "straw effigy",
    "paper fan", "pronged fan", "celestial compass",
    "colossal pick", "bonecage", "clockwork", "tentacle scythe",
    "amethyst aegis", "demonic aegis", "draconic aegis",
    "voltaic spear", "demoncrest", "bo staff", "cockatrice staff",
    "forbidden staff", "ghostly staff", "jeweled staff", "platinum staff",
    "raven staff", "shadow staff", "broadsword", "crystalline sword",
    "katana", "oni blade", "shinobi blade", "emerald blade",
    "eternal blade", "obsidian edge", "frog scepter", "jellyfish",
    "koi scepter", "golden pillar",
}
