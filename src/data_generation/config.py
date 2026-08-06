"""Data generation configuration — machines, sensors, materials, scenarios, constraints.

All constants from design doc §3.2, §4.1, §5.1. Used by telemetry_generator, event_generator,
gold_view_builder, and reference_data.
"""

from __future__ import annotations

from dataclasses import dataclass

# =============================================================================
# 1. MACHINE DEFINITIONS (19 machines in flow order)
# =============================================================================

@dataclass(frozen=True)
class MachineDef:
    """Machine definition for data generation."""

    id: str
    name: str
    type: str
    line_position: int
    nominal_speed: float | None  # BPM or m/min; None for cycle_time-based
    has_speed_sensor: bool


MACHINES: list[MachineDef] = [
    MachineDef("DEPALLETIZER", "Depalletizer", "depalletizer", 1, None, False),
    MachineDef("CONVEYOR_1", "Conveyor 1", "conveyor", 2, 25.0, True),  # belt_speed m/min
    MachineDef("UNCASER", "Uncaser", "uncaser", 3, None, False),
    MachineDef("BOTTLE_WASHER", "Bottle Washer", "washer", 4, 25.0, True),  # tunnel_speed m/min
    MachineDef("EMPTY_INSPECTOR", "Empty Bottle Inspector", "inspector", 5, None, False),
    MachineDef("CONVEYOR_2", "Conveyor 2", "conveyor", 6, 25.0, True),
    MachineDef("FILLER", "Filler", "filler", 7, 420.0, True),  # carousel_speed BPM
    MachineDef("CAPPER", "Capper", "capper", 8, 420.0, True),  # carousel_speed BPM
    MachineDef("PASTEURIZER", "Pasteurizer", "pasteurizer", 9, None, False),
    MachineDef("LABELLER", "Labeller", "labeller", 10, 420.0, True),  # carousel_speed BPM
    MachineDef("CODER", "Coder", "coder", 11, None, False),
    MachineDef("CONVEYOR_3", "Conveyor 3", "conveyor", 12, 25.0, True),
    MachineDef("CASE_PACKER", "Case Packer", "packer", 13, 17.5, True),  # cases/min
    MachineDef("SHRINK_WRAPPER", "Shrink Wrapper", "shrink", 14, None, False),
    MachineDef("CASE_SEALER", "Case Sealer", "sealer", 15, None, False),
    MachineDef("CHECKWEIGHER", "Checkweigher", "checkweigher", 16, None, False),
    MachineDef("CONVEYOR_4", "Conveyor 4", "conveyor", 17, 25.0, True),
    MachineDef("PALLETIZER", "Palletizer", "palletizer", 18, None, False),
    MachineDef("STRETCH_WRAPPER", "Stretch Wrapper", "stretch", 19, None, False),
]


# =============================================================================
# 2. SENSOR DEFINITIONS (66 sensors)
# =============================================================================

@dataclass(frozen=True)
class SensorDef:
    """Sensor definition for machine_telemetry schema."""

    machine_id: str
    sensor_name: str
    sensor_type: str  # speed | counter | process | level | quality
    unit: str
    nominal_value: float | None = None
    alarm_low: float | None = None
    alarm_high: float | None = None


def _sensor(
    machine_id: str,
    sensor_name: str,
    sensor_type: str,
    unit: str,
    nominal_value: float | None = None,
    alarm_low: float | None = None,
    alarm_high: float | None = None,
) -> SensorDef:
    return SensorDef(
        machine_id=machine_id,
        sensor_name=sensor_name,
        sensor_type=sensor_type,
        unit=unit,
        nominal_value=nominal_value,
        alarm_low=alarm_low,
        alarm_high=alarm_high,
    )


SENSORS: list[SensorDef] = [
    # DEPALLETIZER
    _sensor("DEPALLETIZER", "depal_in_count", "counter", "EA"),
    _sensor("DEPALLETIZER", "depal_out_count", "counter", "EA"),
    # Nominal matters more here than it looks. The synthesizer jitters a process
    # sensor around its nominal and falls back to 0 when there is none, which
    # made this emit uniform(-1, 1) — a negative cycle time, on the one sensor
    # DEPALLETIZER is primary-mapped to. Diagnosing a stop on this machine then
    # found no usable signal and reported low confidence, correctly.
    # 12 s/layer feeds the line's 420 BPM at ~84 bottles a layer; below 9 s is
    # implausible and above 18 s starves the filler.
    _sensor("DEPALLETIZER", "depal_cycle_time", "process", "sec", 12.0, 9.0, 18.0),
    # CONVEYOR_1
    # Speed sensors carried a setpoint but no alarm band, and the band is what
    # estimate_rul reads as its threshold — without one the tool has no limit to
    # project a trend against and the maintenance specialist cannot answer.
    # Belts: ±20 % of setpoint. Carousels: floor at 380 BPM, ceiling at the
    # line's own max_speed_bpm constraint (C001, 450).
    _sensor("CONVEYOR_1", "conv1_belt_speed", "speed", "m/min", 25.0, 20.0, 30.0),
    _sensor("CONVEYOR_1", "conv1_jam_count", "counter", "EA"),
    # UNCASER
    _sensor("UNCASER", "uncaser_in_count", "counter", "EA"),
    _sensor("UNCASER", "uncaser_out_count", "counter", "EA"),
    _sensor("UNCASER", "uncaser_breakage_count", "quality", "EA"),
    # BOTTLE_WASHER
    _sensor("BOTTLE_WASHER", "washer_in_count", "counter", "EA"),
    _sensor("BOTTLE_WASHER", "washer_out_count", "counter", "EA"),
    _sensor("BOTTLE_WASHER", "washer_tunnel_speed", "speed", "m/min", 25.0, 20.0, 30.0),
    _sensor("BOTTLE_WASHER", "washer_caustic_temp", "process", "°C", 80, 70, 90),
    _sensor("BOTTLE_WASHER", "washer_caustic_level", "level", "%", 85, 60, None),
    # EMPTY_INSPECTOR
    _sensor("EMPTY_INSPECTOR", "insp_in_count", "counter", "EA"),
    _sensor("EMPTY_INSPECTOR", "insp_out_count", "counter", "EA"),
    _sensor("EMPTY_INSPECTOR", "insp_vision_nok_count", "quality", "EA/min", 2, None, 10),
    _sensor("EMPTY_INSPECTOR", "insp_reject_count", "quality", "EA"),
    # CONVEYOR_2
    _sensor("CONVEYOR_2", "conv2_belt_speed", "speed", "m/min", 25.0, 20.0, 30.0),
    _sensor("CONVEYOR_2", "conv2_accumulation_pressure", "process", "%", 20, None, 80),
    # FILLER
    _sensor("FILLER", "filler_in_count", "counter", "EA"),
    _sensor("FILLER", "filler_out_count", "counter", "EA"),
    _sensor("FILLER", "filler_carousel_speed", "speed", "BPM", 420, 380, 450),
    _sensor("FILLER", "filler_fill_volume_avg", "process", "ml", 330, 325, 335),
    _sensor("FILLER", "filler_fill_low_alarm_count", "quality", "EA"),
    _sensor("FILLER", "filler_bowl_level", "level", "%", 75, 30, None),
    # CAPPER
    _sensor("CAPPER", "capper_in_count", "counter", "EA"),
    _sensor("CAPPER", "capper_out_count", "counter", "EA"),
    _sensor("CAPPER", "capper_carousel_speed", "speed", "BPM", 420, 380, 450),
    _sensor("CAPPER", "capper_torque_avg", "process", "Nm", 2.0, 1.5, 2.5),
    _sensor("CAPPER", "capper_cap_hopper_level", "level", "%", 80, 15, None),
    # PASTEURIZER
    _sensor("PASTEURIZER", "past_in_count", "counter", "EA"),
    _sensor("PASTEURIZER", "past_out_count", "counter", "EA"),
    _sensor("PASTEURIZER", "past_zone2_temp", "process", "°C", 72, 68, 76),
    _sensor("PASTEURIZER", "past_pu_value_actual", "process", "PU", 25, 15, 40),
    # LABELLER
    _sensor("LABELLER", "label_in_count", "counter", "EA"),
    _sensor("LABELLER", "label_out_count", "counter", "EA"),
    _sensor("LABELLER", "label_carousel_speed", "speed", "BPM", 420, 380, 450),
    _sensor("LABELLER", "label_vision_nok_count", "quality", "EA/min", 2, None, 10),
    _sensor("LABELLER", "label_glue_temp", "process", "°C", 145, 135, 155),
    _sensor("LABELLER", "label_roll_remaining", "level", "%", 80, 15, None),
    # CODER
    _sensor("CODER", "coder_in_count", "counter", "EA"),
    _sensor("CODER", "coder_out_count", "counter", "EA"),
    _sensor("CODER", "coder_ink_level", "level", "%", 70, 15, None),
    # CONVEYOR_3
    _sensor("CONVEYOR_3", "conv3_belt_speed", "speed", "m/min", 25.0, 20.0, 30.0),
    _sensor("CONVEYOR_3", "conv3_accumulation_pressure", "process", "%", 20, None, 80),
    # CASE_PACKER
    _sensor("CASE_PACKER", "packer_in_count", "counter", "EA"),
    _sensor("CASE_PACKER", "packer_out_count", "counter", "EA"),
    _sensor("CASE_PACKER", "packer_speed", "speed", "cases/min", 17.5, 14.0, 20.0),
    _sensor("CASE_PACKER", "packer_reject_case_count", "quality", "EA"),
    _sensor("CASE_PACKER", "packer_carton_magazine_level", "level", "%", 75, 15, None),
    # SHRINK_WRAPPER
    _sensor("SHRINK_WRAPPER", "shrink_in_count", "counter", "EA"),
    _sensor("SHRINK_WRAPPER", "shrink_out_count", "counter", "EA"),
    _sensor("SHRINK_WRAPPER", "shrink_film_remaining", "level", "%", 80, 15, None),
    # CASE_SEALER
    _sensor("CASE_SEALER", "sealer_in_count", "counter", "EA"),
    _sensor("CASE_SEALER", "sealer_out_count", "counter", "EA"),
    # CHECKWEIGHER
    _sensor("CHECKWEIGHER", "chkwt_in_count", "counter", "EA"),
    _sensor("CHECKWEIGHER", "chkwt_out_count", "counter", "EA"),
    _sensor("CHECKWEIGHER", "chkwt_weight_avg", "process", "g", 7920, 7800, 8100),
    _sensor("CHECKWEIGHER", "chkwt_reject_count", "quality", "EA"),
    # CONVEYOR_4
    _sensor("CONVEYOR_4", "conv4_belt_speed", "speed", "m/min", 25.0, 20.0, 30.0),
    _sensor("CONVEYOR_4", "conv4_accumulation_pressure", "process", "%", 20, None, 80),
    # PALLETIZER
    _sensor("PALLETIZER", "pallet_in_count", "counter", "EA"),
    _sensor("PALLETIZER", "pallet_out_count", "counter", "EA"),
    _sensor("PALLETIZER", "pallet_magazine_level", "level", "%", 80, 20, None),
    # STRETCH_WRAPPER
    _sensor("STRETCH_WRAPPER", "stretch_in_count", "counter", "EA"),
    _sensor("STRETCH_WRAPPER", "stretch_out_count", "counter", "EA"),
]


# =============================================================================
# 3. PRIMARY SENSOR MAPPING (design §3.2)
# Maps machine_id → (primary_sensor_1, primary_sensor_2, material_level_pct)
# =============================================================================

@dataclass(frozen=True)
class PrimarySensorMapping:
    """Primary sensor mapping for machine_state gold view."""

    primary_sensor_1: str | None
    primary_sensor_2: str | None
    material_level_pct: str | None


PRIMARY_SENSOR_MAPPING: dict[str, PrimarySensorMapping] = {
    "DEPALLETIZER": PrimarySensorMapping("depal_cycle_time", None, None),
    "UNCASER": PrimarySensorMapping(None, None, None),
    "BOTTLE_WASHER": PrimarySensorMapping(
        "washer_caustic_temp", "washer_tunnel_speed", "washer_caustic_level"
    ),
    "EMPTY_INSPECTOR": PrimarySensorMapping(
        "insp_vision_nok_count", "insp_reject_count", None
    ),
    "FILLER": PrimarySensorMapping(
        "filler_fill_volume_avg", "filler_fill_low_alarm_count", "filler_bowl_level"
    ),
    "CAPPER": PrimarySensorMapping("capper_torque_avg", None, "capper_cap_hopper_level"),
    "PASTEURIZER": PrimarySensorMapping(
        "past_zone2_temp", "past_pu_value_actual", None
    ),
    "LABELLER": PrimarySensorMapping(
        "label_vision_nok_count", "label_glue_temp", "label_roll_remaining"
    ),
    "CODER": PrimarySensorMapping(None, None, "coder_ink_level"),
    "CASE_PACKER": PrimarySensorMapping(
        "packer_reject_case_count", "packer_speed", "packer_carton_magazine_level"
    ),
    "SHRINK_WRAPPER": PrimarySensorMapping(None, None, "shrink_film_remaining"),
    "CASE_SEALER": PrimarySensorMapping(None, None, None),
    "CHECKWEIGHER": PrimarySensorMapping(
        "chkwt_weight_avg", "chkwt_reject_count", None
    ),
    "PALLETIZER": PrimarySensorMapping(None, None, "pallet_magazine_level"),
    "STRETCH_WRAPPER": PrimarySensorMapping(None, None, None),
    "CONVEYOR_1": PrimarySensorMapping("conv1_belt_speed", None, None),
    "CONVEYOR_2": PrimarySensorMapping(
        "conv2_belt_speed", "conv2_accumulation_pressure", None
    ),
    "CONVEYOR_3": PrimarySensorMapping(
        "conv3_belt_speed", "conv3_accumulation_pressure", None
    ),
    "CONVEYOR_4": PrimarySensorMapping(
        "conv4_belt_speed", "conv4_accumulation_pressure", None
    ),
}


# =============================================================================
# 4. MATERIALS WITH BOM (~35 materials)
# =============================================================================

@dataclass(frozen=True)
class MaterialDef:
    """Material definition for machine_bom / consumption_log."""

    material_id: str
    material_name: str
    machine_id: str
    unit: str
    consumption_rate_per_hour: float
    waste_factor_pct: float


MATERIALS: list[MaterialDef] = [
    # CAPPER
    MaterialDef("CROWN_STD_26", "Crown caps 26mm", "CAPPER", "EA", 25200, 2.0),
    # LABELLER
    MaterialDef("LABEL_PAPER_FB_LAGER", "Label paper FB Lager", "LABELLER", "EA", 25200, 3.0),
    MaterialDef("GLUE_HOT_MELT", "Hot melt glue", "LABELLER", "g", 500, 1.0),
    # BOTTLE_WASHER
    MaterialDef("CAUSTIC_SODA", "Caustic soda", "BOTTLE_WASHER", "L", 5, 0),
    MaterialDef("RINSE_WATER", "Rinse water", "BOTTLE_WASHER", "L", 200, 0),
    MaterialDef("RINSE_ADDITIVE", "Rinse additive", "BOTTLE_WASHER", "L", 2, 0),
    # SHRINK_WRAPPER
    MaterialDef("FILM_SHRINK_500", "Shrink film 500mm", "SHRINK_WRAPPER", "m", 100, 5.0),
    # CASE_PACKER
    MaterialDef("CARTON_24PACK", "Carton 24-pack", "CASE_PACKER", "EA", 1050, 2.0),
    MaterialDef("TAPE_CARTON", "Carton tape", "CASE_PACKER", "m", 50, 1.0),
    # CODER
    MaterialDef("INK_THERMAL_BLK", "Thermal ink black", "CODER", "ml", 10, 0),
    # PALLETIZER
    MaterialDef("PALLET_EUR_1200", "Euro pallet 1200x800", "PALLETIZER", "EA", 15, 0),
    # STRETCH_WRAPPER
    MaterialDef("STRETCH_FILM_23", "Stretch film 23µm", "STRETCH_WRAPPER", "m", 150, 3.0),
    # FILLER
    MaterialDef("WATER_FILLER", "Process water", "FILLER", "L", 500, 0),
    MaterialDef("CO2_FILLER", "CO2 gas", "FILLER", "kg", 25, 0),
    MaterialDef("PRODUCT_BEER_LAGER", "Beer concentrate Lager", "FILLER", "L", 100, 0),
    MaterialDef("CIP_CHEMICAL", "CIP cleaning chemical", "FILLER", "L", 3, 0),
    # DEPALLETIZER
    MaterialDef("PALLET_STRETCH_IN", "Stretch film (depal)", "DEPALLETIZER", "m", 20, 2.0),
    # UNCASER
    MaterialDef("CRATE_EMPTY_20", "Empty crate 20-bottle", "UNCASER", "EA", 1260, 0.5),
    # CASE_SEALER
    MaterialDef("TAPE_CASE_SEALER", "Case sealing tape", "CASE_SEALER", "m", 80, 2.0),
    # PASTEURIZER
    MaterialDef("STEAM_PAST", "Steam (pasteurizer)", "PASTEURIZER", "kg", 150, 0),
    MaterialDef("COOLING_WATER", "Cooling water", "PASTEURIZER", "L", 300, 0),
    # EMPTY_INSPECTOR
    MaterialDef("COMPRESSED_AIR", "Compressed air", "EMPTY_INSPECTOR", "m³", 5, 0),
    # CHECKWEIGHER
    MaterialDef("CALIBRATION_WEIGHT", "Calibration weight set", "CHECKWEIGHER", "EA", 0.01, 0),
    # Conveyors (minimal)
    MaterialDef("BELT_LUBRICANT", "Belt lubricant", "CONVEYOR_1", "ml", 2, 0),
    # LABELLER (additional)
    MaterialDef("LABEL_PAPER_FB_PILS", "Label paper FB Pilsner", "LABELLER", "EA", 22800, 3.0),
    MaterialDef("LABEL_PAPER_FB_WHEAT", "Label paper FB Wheat", "LABELLER", "EA", 24000, 3.0),
    MaterialDef("LABEL_PAPER_FB_IPA", "Label paper FB IPA", "LABELLER", "EA", 24600, 3.0),
    # CAPPER (additional SKU caps)
    MaterialDef("CROWN_PILS_26", "Crown caps Pilsner", "CAPPER", "EA", 22800, 2.0),
    MaterialDef("CROWN_WHEAT_26", "Crown caps Wheat", "CAPPER", "EA", 24000, 2.0),
    MaterialDef("CROWN_IPA_26", "Crown caps IPA", "CAPPER", "EA", 24600, 2.0),
    # CASE_PACKER (additional carton types)
    MaterialDef("CARTON_12PACK", "Carton 12-pack", "CASE_PACKER", "EA", 1900, 2.0),
    # SHRINK_WRAPPER (additional)
    MaterialDef("FILM_SHRINK_400", "Shrink film 400mm", "SHRINK_WRAPPER", "m", 120, 5.0),
    # PALLETIZER
    MaterialDef("CORNER_BOARDS", "Corner boards", "PALLETIZER", "EA", 60, 1.0),
    MaterialDef("TOP_CAP_BOARD", "Top cap board", "PALLETIZER", "EA", 15, 0),
]


# =============================================================================
# 5. SCENARIO DEFINITIONS (7 scenarios)
# =============================================================================

@dataclass(frozen=True)
class ScenarioDef:
    """Scenario definition for scenario_index / scenario_moments."""

    shift_id: str
    scenario_name: str
    scenario_type: str
    description: str
    key_machines: tuple[str, ...]
    default_moment: str  # ISO timestamp
    shift_start: str  # HH:MM
    shift_end: str  # HH:MM


SCENARIOS: list[ScenarioDef] = [
    ScenarioDef(
        shift_id="shift_1",
        scenario_name="Baseline Smooth",
        scenario_type="baseline",
        description="S1 08:00-16:00, stable operation",
        key_machines=(),
        default_moment="2025-01-01T10:00:00",
        shift_start="08:00",
        shift_end="16:00",
    ),
    ScenarioDef(
        shift_id="shift_2",
        scenario_name="Downtime Triage",
        scenario_type="downtime",
        description="S2 14:00-22:00, LABELLER stop",
        key_machines=("LABELLER",),
        default_moment="2025-01-01T16:15:00",
        shift_start="14:00",
        shift_end="22:00",
    ),
    ScenarioDef(
        shift_id="shift_3",
        scenario_name="Normal Operation",
        scenario_type="normal",
        description="S3 22:00-06:00, steady state",
        key_machines=(),
        default_moment="2025-01-02T02:00:00",
        shift_start="22:00",
        shift_end="06:00",
    ),
    ScenarioDef(
        shift_id="shift_4",
        scenario_name="Schedule Recovery",
        scenario_type="schedule",
        description="S4 06:00-14:00, changeover + fault",
        key_machines=("FILLER",),
        default_moment="2025-01-02T06:45:00",
        shift_start="06:00",
        shift_end="14:00",
    ),
    ScenarioDef(
        shift_id="shift_5",
        scenario_name="Material Shortage",
        scenario_type="shortage",
        description="S5 14:00-22:00, CAPPER cap hopper low",
        key_machines=("CAPPER",),
        default_moment="2025-01-02T19:00:00",
        shift_start="14:00",
        shift_end="22:00",
    ),
    ScenarioDef(
        shift_id="shift_6",
        scenario_name="Quality Deviation",
        scenario_type="quality",
        description="S6 22:00-06:00, FILLER/LABELLER quality hold",
        key_machines=("FILLER", "LABELLER"),
        default_moment="2025-01-03T01:00:00",
        shift_start="22:00",
        shift_end="06:00",
    ),
    ScenarioDef(
        shift_id="shift_7",
        scenario_name="Upstream Delay",
        scenario_type="upstream",
        description="S7 06:00-14:00, FILLER bowl starvation",
        key_machines=("FILLER",),
        default_moment="2025-01-03T06:15:00",
        shift_start="06:00",
        shift_end="14:00",
    ),
]


# =============================================================================
# 6. LINE CONSTRAINTS (~20 constraints)
# =============================================================================

@dataclass(frozen=True)
class LineConstraintDef:
    """Line constraint definition for line_constraints table."""

    constraint_id: str
    category: str
    parameter: str
    value: str
    unit: str
    description: str
    applies_to: str  # "all" or shift_id / machine_id


LINE_CONSTRAINTS: list[LineConstraintDef] = [
    LineConstraintDef(
        "C001", "speed", "max_speed_bpm", "450", "BPM",
        "Max line speed allowed", "all"
    ),
    LineConstraintDef(
        "C002", "speed", "min_speed_bpm", "200", "BPM",
        "Minimum before auto-stop", "all"
    ),
    LineConstraintDef(
        "C003", "overtime", "allow_ot", "false", "-",
        "No overtime allowed for MVP shifts", "all"
    ),
    LineConstraintDef(
        "C004", "quality", "quality_hold_threshold_pct", "10", "%",
        "Hold production if reject rate exceeds", "all"
    ),
    LineConstraintDef(
        "C005", "maintenance", "next_pm_window", "shift_4 06:00", "-",
        "Planned PM at next shift start", "all"
    ),
    LineConstraintDef(
        "C006", "schedule", "changeover_window_shift_4", "06:00-06:45", "-",
        "Planned changeover for S4", "shift_4"
    ),
    LineConstraintDef(
        "C007", "inventory", "crown_safety_stock", "5000", "EA",
        "Safety stock for CROWN_STD_26", "all"
    ),
    LineConstraintDef(
        "C008", "inventory", "crown_reorder_point", "8000", "EA",
        "Reorder when below this", "all"
    ),
    LineConstraintDef(
        "C009", "inventory", "label_roll_safety_stock", "2000", "EA",
        "Safety stock for label rolls", "all"
    ),
    LineConstraintDef(
        "C010", "process", "glue_temp_min", "135", "°C",
        "Min glue temp for LABELLER", "LABELLER"
    ),
    LineConstraintDef(
        "C011", "process", "glue_temp_max", "155", "°C",
        "Max glue temp for LABELLER", "LABELLER"
    ),
    LineConstraintDef(
        "C012", "process", "fill_volume_min", "325", "ml",
        "Min fill volume for FILLER", "FILLER"
    ),
    LineConstraintDef(
        "C013", "process", "fill_volume_max", "335", "ml",
        "Max fill volume for FILLER", "FILLER"
    ),
    LineConstraintDef(
        "C014", "process", "pasteurizer_pu_min", "15", "PU",
        "Min PU value for pasteurizer", "PASTEURIZER"
    ),
    LineConstraintDef(
        "C015", "process", "pasteurizer_pu_max", "40", "PU",
        "Max PU value for pasteurizer", "PASTEURIZER"
    ),
    LineConstraintDef(
        "C016", "process", "conveyor_accumulation_max", "80", "%",
        "Max accumulation pressure before backup alarm", "all"
    ),
    LineConstraintDef(
        "C017", "inventory", "carton_safety_stock", "2000", "EA",
        "Safety stock for cartons", "all"
    ),
    LineConstraintDef(
        "C018", "inventory", "pallet_safety_stock", "50", "EA",
        "Safety stock for pallets", "all"
    ),
    LineConstraintDef(
        "C019", "schedule", "max_changeover_minutes", "45", "min",
        "Max allowed changeover duration", "all"
    ),
    LineConstraintDef(
        "C020", "quality", "vision_nok_baseline", "2", "EA/min",
        "Baseline vision NOK rate for degraded threshold", "all"
    ),
]


# =============================================================================
# 7. SKU DEFINITIONS (with ideal_rate_bpm)
# =============================================================================

@dataclass(frozen=True)
class SkuDef:
    """SKU definition for production_schedule."""

    sku_id: str
    sku_name: str
    ideal_rate_bpm: float


SKUS: list[SkuDef] = [
    SkuDef("LAGER_330_GLASS_24", "Lager 330ml Glass 24-pack", 420),
    SkuDef("PILSNER_500_GLASS_12", "Pilsner 500ml Glass 12-pack", 380),
    SkuDef("WHEAT_330_GLASS_24", "Wheat 330ml Glass 24-pack", 400),
    SkuDef("IPA_330_GLASS_24", "IPA 330ml Glass 24-pack", 410),
]


# =============================================================================
# 8. STATUS DERIVATION THRESHOLDS
# =============================================================================

ALARM_COUNT_DEGRADED_THRESHOLD = 3  # alarm_count > 3× baseline → degraded
SPEED_ZERO_THRESHOLD = 0.01
COUNTER_DELTA_ZERO_THRESHOLD = 0


# =============================================================================
# 9. COLOR MAPPING (for line schematic)
# =============================================================================

STATUS_COLORS: dict[str, str] = {
    "running": "#22c55e",
    "stopped": "#ef4444",
    "degraded": "#eab308",
    "starved": "#9ca3af",
    "idle": "#3b82f6",
}
