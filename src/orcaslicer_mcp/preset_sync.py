"""Convert a preset read from the slicer API into OrcaSlicer's own user-preset file format
and compare it with the copy kept by the home print queue (Gestor 3D); and turn such a file
back into the text the slicer API takes.

The API returns every option as ConfigBase::opt_serialize text. Orca's save_to_json writes
plain strings for coString options, arrays of per-item strings for vector options and the
serialized text for every other scalar; load_from_json reads either form back."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import settings_schema
from .guard import REDACTED

GESTOR_KIND = {"print": "process", "filament": "filament", "printer": "machine"}
SETTINGS_ID_KEY = {"print": "print_settings_id", "filament": "filament_settings_id",
                   "printer": "printer_settings_id"}
FILE_ONLY_KEYS = frozenset({"name", "from", "version", "type", "inherits", "is_custom_defined",
                            "instantiation", "setting_id", "filament_id", "description", "url",
                            "renamed_from"})
_TYPE_OVERRIDES = {"print_settings_id": "coString", "printer_settings_id": "coString",
                   "filament_settings_id": "coStrings", "filament_ids": "coStrings",
                   "filament_multi_colour": "coStrings", "filament_colour_type": "coStrings"}
_COMMA_VECTORS = frozenset({"coFloats", "coInts", "coBools", "coPercents", "coFloatsOrPercents",
                            "coEnums", "coPoints"})
_RAW_STRING_EXCEPTIONS = frozenset({"bed_custom_texture", "bed_custom_model"})
_FILAMENT_PREFIX = "filament_"
_FILAMENT_EXTRUDER_OVERRIDE_KEYS = frozenset(_FILAMENT_PREFIX + key for key in (
    "retraction_length", "z_hop", "z_hop_types", "retract_lift_above", "retract_lift_below",
    "retract_lift_enforce", "retraction_speed", "deretraction_speed", "retract_restart_extra",
    "retraction_minimum_travel", "retract_length_toolchange", "retract_restart_extra_toolchange",
    "wipe_distance", "retract_when_changing_layer", "wipe", "retract_before_wipe",
    "retract_after_wipe", "long_retractions_when_cut", "retraction_distances_when_cut"))
_MACHINE_LIMIT_KEYS = frozenset(f"machine_max_{limit}_{axis}"
                                for limit in ("speed", "acceleration", "jerk") for axis in "xyze")


def option_type(key: str) -> str | None:
    if key in _TYPE_OVERRIDES:
        return _TYPE_OVERRIDES[key]
    record = settings_schema.describe(key)
    if record:
        return record.get("type")
    return _generated_option_type(key)


def _generated_option_type(key: str) -> str | None:
    """Options PrintConfig.cpp declares in loops, which the extracted schema does not hold."""
    if key in _MACHINE_LIMIT_KEYS:
        return "coFloats"
    if key in _FILAMENT_EXTRUDER_OVERRIDE_KEYS:
        return option_type(key.removeprefix(_FILAMENT_PREFIX))
    return None


def unescape_string(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            i += 1
            c = {"n": "\n", "r": "\r", "t": "\t"}.get(text[i], text[i])
        out.append(c)
        i += 1
    return "".join(out)


def unescape_strings(text: str) -> list[str]:
    """Inverse of Orca's escape_strings_cstyle: ';'-separated items, quoted when complex."""
    if text == "":
        return []
    items, i, n = [], 0, len(text)
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i == n:
            return items
        buf = []
        if text[i] == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                    buf.append({"n": "\n", "r": "\r"}.get(text[i], text[i]))
                else:
                    buf.append(text[i])
                i += 1
            i += 1
        else:
            while i < n and text[i] != ";":
                buf.append(text[i])
                i += 1
        items.append("".join(buf))
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return items
        i += 1
        if i == n:
            items.append("")
            return items


def from_api(key: str, serialized: str) -> str | list[str]:
    """The value as Orca's save_to_json would write it."""
    kind = option_type(key)
    if kind == "coStrings":
        return unescape_strings(serialized)
    if kind in _COMMA_VECTORS:
        return serialized.split(",") if serialized else []
    if kind == "coPointsGroups":
        return serialized.split("#") if serialized else []
    if kind == "coString" and key not in _RAW_STRING_EXCEPTIONS:
        return unescape_string(serialized)
    return serialized


def escape_string(text: str) -> str:
    """Orca's escape_string_cstyle."""
    return (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\r", "\\r").replace("\n", "\\n"))


def escape_strings(items: list[str]) -> str:
    """Orca's escape_strings_cstyle, the inverse of unescape_strings."""
    quoted = []
    for item in items:
        complex_item = (len(items) == 1 and item == "") or any(c in item for c in ' \t;\\"\r\n')
        quoted.append('"' + escape_string(item) + '"' if complex_item else item)
    return ";".join(quoted)


def to_api(key: str, value) -> str:
    """The text the slicer API takes for a value as a preset file holds it."""
    kind = option_type(key)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        items = [str(v) for v in value]
        if kind == "coStrings":
            return escape_strings(items)
        return ("#" if kind == "coPointsGroups" else ",").join(items)
    text = str(value)
    if kind == "coString" and key not in _RAW_STRING_EXCEPTIONS:
        return escape_string(text)
    return text


def canonical(key: str, value) -> tuple | str | None:
    """Comparable form of a file value; a vector written as one string is split first."""
    if value is None:
        return None
    if isinstance(value, str) and option_type(key) in _COMMA_VECTORS | {"coStrings", "coPointsGroups"}:
        value = from_api(key, value)
    if isinstance(value, list):
        return tuple(_number_or_text(str(v)) for v in value)
    return _number_or_text(str(value))


def _number_or_text(text: str):
    try:
        number = float(text)
    except ValueError:
        return text
    return text if number != number else number


_VARIANT_KEY = "filament_extruder_variant"
_PER_VARIANT_KEYS = frozenset({
    "activate_air_filtration", "activate_air_filtration_during_print",
    "activate_air_filtration_on_completion", "adaptive_pressure_advance",
    "adaptive_pressure_advance_bridges", "adaptive_pressure_advance_model",
    "adaptive_pressure_advance_overhangs", "additional_cooling_fan_speed",
    "complete_print_exhaust_fan_speed", "during_print_exhaust_fan_speed",
    "enable_pressure_advance", "fan_max_speed", "fan_min_speed",
    "filament_adaptive_volumetric_speed", "filament_cooling_before_tower",
    "filament_deretraction_speed", _VARIANT_KEY, "filament_flow_ratio",
    "filament_flush_temp", "filament_flush_volumetric_speed", "filament_ironing_flow",
    "filament_ironing_inset", "filament_ironing_spacing", "filament_ironing_speed",
    "filament_long_retractions_when_cut", "filament_max_volumetric_speed",
    "filament_minimal_purge_on_wipe_tower", "filament_multitool_ramming",
    "filament_multitool_ramming_flow", "filament_multitool_ramming_volume",
    "filament_pre_cooling_temperature", "filament_pre_cooling_temperature_nc",
    "filament_preheat_temperature_delta", "filament_ramming_travel_time",
    "filament_ramming_travel_time_nc", "filament_ramming_volumetric_speed",
    "filament_ramming_volumetric_speed_nc", "filament_retract_after_wipe",
    "filament_retract_before_wipe", "filament_retract_length_nc",
    "filament_retract_length_toolchange", "filament_retract_lift_above",
    "filament_retract_lift_below", "filament_retract_lift_enforce",
    "filament_retract_restart_extra", "filament_retract_restart_extra_toolchange",
    "filament_retract_when_changing_layer", "filament_retraction_distances_when_cut",
    "filament_retraction_length", "filament_retraction_minimum_travel",
    "filament_retraction_speed", "filament_wipe", "filament_wipe_distance", "filament_z_hop",
    "filament_z_hop_types", "long_retractions_when_ec", "nozzle_temperature",
    "nozzle_temperature_initial_layer", "nozzle_temperature_range_high",
    "nozzle_temperature_range_low", "pressure_advance", "retraction_distances_when_ec",
    "volumetric_speed_coefficients"})


_PER_VARIANT_PRINTER_KEYS = frozenset({"hotend_cooling_rate", "hotend_heating_rate"})
_PER_VARIANT_PAIR_KEYS = _MACHINE_LIMIT_KEYS | frozenset({
    "machine_max_acceleration_extruding", "machine_max_acceleration_retracting",
    "machine_max_acceleration_travel", "machine_max_junction_deviation"})
_ONE_VERSION_KEYS = frozenset({"extruder_nozzle_stats", "silent_mode"})
PER_VARIANT_TYPES = frozenset({"filament", "printer"})
_UNSET_ITEM = "nil"
GESTOR_OWN_KEYS = {"printer": frozenset({
    "machine_start_gcode", "support_chamber_temp_control", "print_host", "print_host_webui",
    "default_filament_profile", "default_print_profile", "file_start_gcode"})}


def gestor_own_keys(ptype: str) -> frozenset:
    """What each side has to hold its own way and no copy may level: the Gestor 3D slices
    headless with OrcaSlicer 2.4 and its queue drives the chamber, the PC runs 2.5 and prints
    through that queue."""
    return GESTOR_OWN_KEYS.get(ptype, frozenset())


def same_value(key: str, a, b, per_variant: bool = False) -> bool:
    """Equal as effective settings. OrcaSlicer 2.5 keeps per extruder variant what 2.4 kept
    once, so with per_variant the short form of 2.4 matches the long one of 2.5 for three
    explicit sets of keys (PrintConfig.cpp): the filament vectors of
    filament_options_with_variant (_PER_VARIANT_KEYS) and the hotend rates of
    printer_options_with_variant_1, where one item matches a vector repeating it (a one-item
    variant list matches a variant list it starts); and the machine limits of
    printer_options_with_variant_2, where the normal,silent pair matches a vector that starts
    with it and goes on repeating the pair or its first value, the two ways 2.5 fills them.
    A hotend rate 2.5 stores as nil for a variant is unset there and matches any item."""
    ca, cb = canonical(key, a), canonical(key, b)
    if ca == cb:
        return True
    if not per_variant or not (isinstance(ca, tuple) and isinstance(cb, tuple)):
        return False
    short, long = sorted((ca, cb), key=len)
    if key in _PER_VARIANT_PAIR_KEYS:
        return _pair_fills(short, long)
    if key not in _PER_VARIANT_KEYS | _PER_VARIANT_PRINTER_KEYS or len(short) != 1 or not long:
        return False
    if key == _VARIANT_KEY:
        return long[0] == short[0]
    if key in _PER_VARIANT_PRINTER_KEYS:
        long = tuple(item for item in long if item != _UNSET_ITEM)
        return bool(long) and all(item == short[0] for item in long)
    return all(item == short[0] for item in long)


def _pair_fills(pair: tuple, filled: tuple) -> bool:
    if len(pair) != 2 or len(filled) <= 2 or len(filled) % 2:
        return False
    return filled[:2] == pair and (filled == pair * (len(filled) // 2)
                                   or all(item == pair[0] for item in filled[2:]))


def only_in_one_version(key: str, a, b, per_variant: bool = False) -> bool:
    """A key OrcaSlicer 2.5 added or dropped, held by one side only: not a difference in the
    settings, and nothing the other version can be given."""
    return per_variant and key in _ONE_VERSION_KEYS and (a is None) != (b is None)


def fit_to_current(key: str, target, current, per_variant: bool = False):
    """A short file vector written over a per-variant vector keeps the slicer length, and a
    machine limit pair the way the slicer vector is filled: with its first value when it is,
    with the pair otherwise."""
    if not (per_variant and isinstance(target, list) and isinstance(current, list)):
        return target
    if key in _PER_VARIANT_PAIR_KEYS:
        if len(target) != 2 or len(current) <= 2 or len(current) % 2:
            return target
        repeats = len(current) // 2
        first_value_fills = (current != current[:2] * repeats
                             and all(item == current[0] for item in current[2:]))
        return target + [target[0]] * (len(current) - 2) if first_value_fills else target * repeats
    if (key in _PER_VARIANT_KEYS | _PER_VARIANT_PRINTER_KEYS and key != _VARIANT_KEY
            and len(target) == 1 and len(current) > 1):
        return target * len(current)
    return target


@dataclass
class Difference:
    key: str
    pc: object
    gestor: object


@dataclass
class Comparison:
    differences: list[Difference] = field(default_factory=list)
    parent_known: bool = True
    kept_apart: list[Difference] = field(default_factory=list)


def build_file(ptype: str, name: str, pc_config: dict, parent_config: dict | None,
               existing: dict | None = None) -> dict:
    """User-preset file holding only what differs from the parent, like Orca's own export.
    A preset without a parent (or whose parent the slicer cannot read) is written in full.
    Redacted credentials keep the value the existing file had, or are left out."""
    existing = existing or {}
    parent_config = parent_config or {}
    out: dict = {"from": "User", "name": name, "inherits": pc_config.get("inherits", "") or ""}
    if existing.get("version"):
        out["version"] = existing["version"]
    for key, serialized in pc_config.items():
        if key in FILE_ONLY_KEYS:
            continue
        if serialized == REDACTED or (isinstance(serialized, str) and REDACTED in serialized):
            if key in existing:
                out[key] = existing[key]
            continue
        value = from_api(key, serialized)
        if key in parent_config and canonical(key, value) == canonical(key, from_api(key, parent_config[key])):
            continue
        out[key] = value
    id_key = SETTINGS_ID_KEY[ptype]
    out[id_key] = [name] if option_type(id_key) == "coStrings" else name
    return out


def dump_file(preset: dict) -> bytes:
    return (json.dumps(preset, indent="\t", sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def target_changes(gestor_file: dict, start_config: dict, parent_config: dict,
                   per_variant: bool = False, own_keys: frozenset = frozenset()) -> dict:
    """What to write over start_config (the preset selected in the slicer) so it holds the
    Gestor file laid over parent_config, as API text. Keys whose current value is a redacted
    credential are left alone, own_keys stay as the slicer has them, and saving sets the
    settings ids."""
    skip = FILE_ONLY_KEYS | set(SETTINGS_ID_KEY.values()) | own_keys
    changes = {}
    for key in sorted((set(gestor_file) | set(start_config)) - skip):
        current = start_config.get(key)
        if current is not None and REDACTED in str(current):
            continue
        if key in gestor_file:
            target = gestor_file[key]
        elif key in parent_config:
            target = from_api(key, parent_config[key])
        else:
            continue
        current_value = None if current is None else from_api(key, current)
        if only_in_one_version(key, target, current_value, per_variant):
            continue
        if not same_value(key, target, current_value, per_variant):
            changes[key] = to_api(key, fit_to_current(key, target, current_value, per_variant))
    return changes


def compare(pc_config: dict, gestor_file: dict, gestor_parent: dict | None,
            per_variant: bool = False, own_keys: frozenset = frozenset()) -> Comparison:
    """Effective values on both sides: the PC's full config against the Gestor file laid over
    its parent as read from the PC (system presets match; a 2.5 PC holds some filament and
    printer vectors per extruder variant where the Gestor's 2.4 files hold one item or pair,
    which same_value accepts with per_variant, set for filament and printer presets, and the
    keys only one of the two versions has are left out).
    Without that parent only the keys the Gestor file states are compared. A different value
    of one of own_keys is listed in kept_apart, not as a difference."""
    result = Comparison(parent_known=gestor_parent is not None)
    pc_inherits = pc_config.get("inherits", "") or ""
    gestor_inherits = gestor_file.get("inherits", "") or ""
    if pc_inherits != gestor_inherits:
        result.differences.append(Difference("inherits", pc_inherits, gestor_inherits))
    parent = gestor_parent or {}
    keys = set(gestor_file) | (set(pc_config) if gestor_parent is not None else set())
    for key in sorted(keys - FILE_ONLY_KEYS):
        pc_raw = pc_config.get(key)
        if pc_raw is not None and REDACTED in str(pc_raw):
            continue
        pc_value = None if pc_raw is None else from_api(key, pc_raw)
        if key in gestor_file:
            gestor_value = gestor_file[key]
        elif key in parent:
            gestor_value = from_api(key, parent[key])
        else:
            gestor_value = None
        if only_in_one_version(key, pc_value, gestor_value, per_variant):
            continue
        if not same_value(key, pc_value, gestor_value, per_variant):
            found = result.kept_apart if key in own_keys else result.differences
            found.append(Difference(key, pc_value, gestor_value))
    return result
