"""Test input validation used by context_builder. Imports from validation module (no databricks/langchain)."""

from __future__ import annotations

import pytest

from agent_server.validation import validate_machine_id, validate_shift_id, validate_timestamp


class TestShiftIdValidation:
    def test_valid_shift_id(self):
        assert validate_shift_id("shift_1") == "shift_1"
        assert validate_shift_id("shift-5") == "shift-5"

    def test_invalid_shift_id_sql_injection(self):
        with pytest.raises(ValueError):
            validate_shift_id("shift_1'; DROP TABLE --")

    def test_invalid_shift_id_empty(self):
        with pytest.raises(ValueError):
            validate_shift_id("")


class TestTimestampValidation:
    def test_valid_timestamp_plain(self):
        assert validate_timestamp("2025-01-01T14:00:00") == "2025-01-01T14:00:00"

    def test_valid_timestamp_with_millis(self):
        assert validate_timestamp("2025-01-01T14:00:00.000") == "2025-01-01T14:00:00"

    def test_valid_timestamp_with_z_suffix(self):
        assert validate_timestamp("2025-01-01T14:00:00.000Z") == "2025-01-01T14:00:00"
        assert validate_timestamp("2025-01-01T14:00:00Z") == "2025-01-01T14:00:00"

    def test_invalid_timestamp_sql_injection(self):
        with pytest.raises(ValueError):
            validate_timestamp("2025-01-01'; DROP TABLE --")

    def test_invalid_timestamp_garbage(self):
        with pytest.raises(ValueError):
            validate_timestamp("not-a-timestamp")


class TestMachineIdValidation:
    def test_valid_machine_id(self):
        assert validate_machine_id("CONVEYOR_1") == "CONVEYOR_1"
        assert validate_machine_id("FILLER") == "FILLER"

    def test_none_returns_none(self):
        assert validate_machine_id(None) is None
        assert validate_machine_id("") is None

    def test_invalid_machine_id_sql_injection(self):
        with pytest.raises(ValueError):
            validate_machine_id("FILLER'; DROP TABLE --")
