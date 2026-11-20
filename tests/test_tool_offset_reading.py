import pytest

import orcaslicer_mcp.server as srv
from orcaslicer_mcp import tool_offset_test as tt
from orcaslicer_mcp.knowledge_index import search_knowledge


def test_offset_is_minus_reading_times_step_and_correction_its_opposite():
    measured = tt.read_tool_offset(2, -1.5, 0.05)
    assert (measured.x.offset_mm, measured.x.correction_mm) == (-0.1, 0.1)
    assert (measured.y.offset_mm, measured.y.correction_mm) == (0.075, -0.075)
    assert measured.step == 0.05 and not measured.lined_up
    assert not measured.x.at_scale_end and not measured.y.at_scale_end


def test_reading_matches_the_pair_the_pattern_lines_up():
    step, displacement = 0.05, -0.1
    gaps = {k: abs(k * (tt.REFERENCE_PITCH + step) + displacement - k * tt.REFERENCE_PITCH)
            for k in range(-tt.TEETH_PER_SIDE, tt.TEETH_PER_SIDE + 1)}
    lined_up = min(gaps, key=gaps.get)
    assert tt.read_tool_offset(lined_up, 0, step).x.offset_mm == pytest.approx(displacement)


def test_default_step_zero_reading_and_scale_ends():
    centred = tt.read_tool_offset(0, 0)
    assert centred.step == tt.DEFAULT_STEP and centred.lined_up
    assert repr(centred.x.offset_mm) == "0.0" and repr(centred.x.correction_mm) == "0.0"
    ends = tt.read_tool_offset(10, -10, 0.1)
    assert (ends.x.offset_mm, ends.y.offset_mm) == (-1.0, 1.0)
    assert ends.x.at_scale_end and ends.y.at_scale_end


@pytest.mark.parametrize("x, y, axis, reason", [
    (10.5, 0, "X", "between -10 and 10"),
    (0, -11, "Y", "between -10 and 10"),
    (float("nan"), 0, "X", "between -10 and 10"),
    (0, float("inf"), "Y", "between -10 and 10"),
    (0.3, 0, "X", "whole tooth or a half"),
    (0, 1.25, "Y", "whole tooth or a half"),
    ("two", 0, "X", "number of teeth"),
])
def test_readings_outside_the_pattern_are_refused(x, y, axis, reason):
    with pytest.raises(tt.ToolOffsetTestError, match=f"the {axis} reading must be .*{reason}"):
        tt.read_tool_offset(x, y, 0.05)


def test_out_of_range_message_names_the_range_in_mm():
    with pytest.raises(tt.ToolOffsetTestError, match=r"\u00b10\.5 mm at a step of 0\.05 mm\), got 12"):
        tt.read_tool_offset(12, 0, 0.05)


async def test_tool_returns_offset_correction_and_how_to_correct(monkeypatch):
    monkeypatch.setattr(srv, "_client", lambda: pytest.fail("the reading must not call OrcaSlicer"))
    out = await srv.read_tool_offset_calibration(reference_tool=1, tool=3, x_reading=2, y_reading=-1.5, step=0.05)
    assert out["reading"] == {"x": 2.0, "y": -1.5} and out["step_mm"] == 0.05
    assert out["offset_mm"] == {"x": -0.1, "y": 0.075}
    assert out["correction_mm"] == {"x": 0.1, "y": -0.075}
    assert out["preset_written"] is False and out["lined_up"] is False
    assert (out["reference_tool"], out["tool"]) == (1, 3) and "error" not in out and "warnings" not in out
    firmware, orca = out["how_to_correct"]
    assert "Extruder Offset" in firmware and "print this test again" in firmware
    assert "head 3" in orca and "leave head 1" in orca
    trim = out["orca_extruder_offset"]
    assert trim["key"] == "extruder_offset" and trim["tool"] == 3
    assert trim["add_mm"] == out["offset_mm"]
    assert "subtracts" in trim["note"] and "put it back" in trim["note"] and "Nothing was written" in trim["note"]


async def test_tool_with_both_centre_pairs_lined_up_has_nothing_to_correct():
    out = await srv.read_tool_offset_calibration(reference_tool=2, tool=1, x_reading=0, y_reading=0)
    assert out["lined_up"] is True and out["step_mm"] == 0.05
    assert out["offset_mm"] == out["correction_mm"] == {"x": 0.0, "y": 0.0}
    assert out["how_to_correct"] == [srv._TOOL_OFFSET_LINED_UP] and "orca_extruder_offset" not in out


async def test_tool_warns_at_the_end_of_a_scale():
    out = await srv.read_tool_offset_calibration(reference_tool=1, tool=2, x_reading=-10, y_reading=1)
    assert out["offset_mm"] == {"x": 0.5, "y": -0.05}
    assert len(out["warnings"]) == 1 and "the X reading is the last pair" in out["warnings"][0]


async def test_tool_refuses_bad_input_with_a_clear_error():
    same = await srv.read_tool_offset_calibration(reference_tool=2, tool=2, x_reading=0, y_reading=0)
    assert same["error"] == "same_tool"
    step = await srv.read_tool_offset_calibration(reference_tool=1, tool=2, x_reading=0, y_reading=0, step=0.5)
    assert step["error"] == "invalid_step" and "between 0.02 and 0.2" in step["detail"]
    far = await srv.read_tool_offset_calibration(reference_tool=1, tool=2, x_reading=11, y_reading=0)
    assert far["error"] == "invalid_reading" and "X reading must be between -10 and 10" in far["detail"]
    assert "offset_mm" not in far
    fine = await srv.read_tool_offset_calibration(reference_tool=1, tool=2, x_reading=0, y_reading=0.25)
    assert fine["error"] == "invalid_reading" and "Y reading must be a whole tooth or a half" in fine["detail"]


async def test_tool_is_read_only_and_the_test_points_at_it():
    tools = {t.name: t for t in await srv.mcp.list_tools()}
    assert tools["read_tool_offset_calibration"].annotations.read_only_hint is True
    assert "read_tool_offset_calibration" in srv._TOOL_OFFSET_READ


def test_knowledge_base_explains_the_offset():
    files = [c.relpath for c in search_knowledge("offset between tool heads")]
    assert "considerations/tool-head-offset.md" in files
