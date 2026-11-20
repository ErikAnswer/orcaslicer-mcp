---
topics: [tool, head, offset, extruder, alignment, multicolor, toolchanger, calibration, vernier]
orca_keys: [extruder_offset]
---

# XY offset between tool heads

## What actually determines it

On a printer with several heads (tool changer, IDEX, dual nozzle) each
head has its own nozzle position, and the difference between them has
to be taken out somewhere or the colours and materials of one part land
shifted against each other: seams between colours open or overlap, a
support interface misses the part, a two-material joint has a step.
The symptom is a constant shift of everything one head prints, the same
on every layer and every part, which tells it apart from ringing,
layer shift or shrinkage.

Exactly one place should hold that difference. Either the printer
firmware applies it when the head is picked (the G-code only carries
`T<n>` and the printer preset keeps `extruder_offset` at 0x0), or the
slicer does, through `extruder_offset` in the printer preset, which
OrcaSlicer subtracts from every coordinate it sends for that head. The
FlashForge Creator 5 Pro is of the first kind: its Extruder Offset
calibration, started from the screen, measures and stores the offsets,
and there is no field to type one. A value in `extruder_offset` there
adds to what the firmware already applies, so it is a residual trim
and becomes wrong the moment the printer is calibrated again.

The offset is measured with a vernier print, not a ruler:
create_tool_offset_calibration puts two scales on the plate, along X
and along Y, each with a comb from the reference head and a facing comb
from the other head whose teeth are one step further apart. The pair of
teeth that forms a straight line, counted from the centre pair and
positive toward the short tab, is the reading; the head prints
-reading x step away from where it should. read_tool_offset_calibration
does that arithmetic for both axes and says how to correct it, without
writing anything.

The test resolves half a step (0.025 mm at the default 0.05 mm) and
reaches ten steps each way. A reading at the last pair means the real
offset may be larger; no pair lining up means it is beyond the test,
and a larger step or the printer's own calibration comes first. The
reading also only holds while the two pieces stay placed against each
other exactly as the tool left them.

## Context -> consideration mappings

- IF the user's situation says colours or materials from different heads are shifted against each other by the same amount everywhere THEN weigh checking the head offset with create_tool_offset_calibration before touching any other setting, because a constant shift per head is an offset error and no flow, speed or compensation setting corrects it.
- IF the user's situation has a reading other than 0 on a printer that stores the head offsets itself (Creator 5 Pro) THEN weigh running the printer's own offset calibration again and reprinting the test over editing `extruder_offset`, because the firmware value is the one in use and a slicer value only stacks on top of it.
- IF the user's situation says the same reading stays after the printer's own calibration THEN weigh adding the measured offset, with its sign, to that head's `extruder_offset` as a residual trim, because OrcaSlicer subtracts the setting from the coordinates; it is the user's decision and it has to return to 0 when the printer is calibrated again.
- IF the user's situation says a printer preset already carries a non-zero `extruder_offset` and the printer was recalibrated since THEN weigh setting it back to 0 and measuring again, because the trim was fitted to the previous firmware offsets.
