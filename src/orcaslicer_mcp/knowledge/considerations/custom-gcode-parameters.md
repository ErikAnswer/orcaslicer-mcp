---
topics: [custom, gcode, custom_parameters_filament, custom_parameters_print, custom_parameters_printer, parameter, parameters, template, placeholder, start, chamber, filament, macro, variable]
orca_keys: [machine_start_gcode]
---

# Custom G-code parameters

## What actually determines it

A value that only one filament, process or printer needs in its custom
G-code (a chamber temperature, a soak time, a park position) does not
need a duplicated profile or a hard-coded number in the start G-code.
The OrcaSlicer MCP build, from erik-v2.5.1-mcp.21, holds the user's own
values in three keys, each a flat JSON object written as text:
`custom_parameters_filament` (Filament > Notes, one per filament),
`custom_parameters_print` and `custom_parameters_printer`. A value may
only be a number or `true`/`false`, for example `{"chamber": 45, "soak": true}`.

A G-code template reads them as `{custom_parameter_filament_<name>}`,
`{custom_parameter_print_<name>}` and `{custom_parameter_printer_<name>}`.
The filament form gives the value of the filament in use where the
template runs (in the start G-code, the first filament printed), or of a
given filament with an index: `{custom_parameter_filament_chamber[1]}` is
the second filament's, counting from 0. Stock OrcaSlicer has none of
this.

The keys are plain data, so set_config and edit_preset write them without
the "Allow script, G-code and connection edits" box; the template that
reads them is G-code and still needs it. Slicing stops with a clear
message when the JSON is not an object, a value
is text, or a template reads a parameter that the filament it reads does
not define. A parameter read without an index from the start G-code must
therefore exist in every filament that can be the first one printed,
not only in the one being tuned.

The Gestor 3D slices headless with stock OrcaSlicer: sync_presets copies a
filament's parameters to it, where they are kept but not used, and the
printer preset is never copied, so its start G-code cannot read them.

## Context -> consideration mappings

- IF the user's situation wants a value per filament in the start or tool change G-code (chamber temperature, a wait, a purge length) THEN weigh a key in `custom_parameters_filament` read by `{custom_parameter_filament_<name>}` over a copy of the printer profile per material, because one template then serves every filament.
- IF the user's situation puts a parameter without an index into the start G-code THEN weigh defining it in every filament that can print first, because slicing stops when the filament in use does not define it.
- IF the user's situation needs the value of a filament other than the one in use THEN weigh the indexed form `{custom_parameter_filament_<name>[n]}`, counting from 0, because the plain form only gives the current filament's.
- IF the user's situation reports a slice stopped by a custom parameter THEN weigh checking that the JSON is one object of numbers and true/false, without quotes around the values, because text values and malformed JSON are refused at slicing, not when written.
