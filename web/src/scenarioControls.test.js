import assert from "node:assert/strict";
import test from "node:test";
import {
	formatOpcPathway,
	formatSlrOptionLabel,
	getAvailableSlrLevels,
	getAvailableStorms,
	hasDeltaForSelection,
	resolveScenario,
} from "./scenarioControls.js";

const SAMPLE_SCENARIOS = [
	{
		id: "sf_citywide_100yr_slr0ft_depth",
		storm: "100yr",
		slr_ft: 0,
		metric: "depth",
		opc_2024_pathway: "Baseline (Present Day)",
	},
	{
		id: "sf_citywide_100yr_slr3ft_depth",
		storm: "100yr",
		slr_ft: 3,
		metric: "depth",
		opc_2024_pathway: "2024 CA OPC Intermediate (~2100)",
	},
	{
		id: "sf_citywide_100yr_slr3ft_delta",
		storm: "100yr",
		slr_ft: 3,
		metric: "delta",
		opc_2024_pathway: "2024 CA OPC Intermediate (~2100)",
	},
	{
		id: "sf_citywide_ar_20221231_depth",
		storm: "ar_20221231",
		slr_ft: 0,
		metric: "depth",
		opc_2024_pathway: "Observed Hindcast (NOAA 9414290)",
	},
];

test("getAvailableStorms and getAvailableSlrLevels extract unique sorted controls", () => {
	const storms = getAvailableStorms(SAMPLE_SCENARIOS);
	assert.deepEqual(
		storms.map((s) => s.id),
		["100yr", "ar_20221231"],
	);
	assert.equal(storms[1].label, "Dec 31, 2022 AR Hindcast");

	assert.deepEqual(getAvailableSlrLevels(SAMPLE_SCENARIOS, "100yr"), [0, 3]);
	assert.deepEqual(getAvailableSlrLevels(SAMPLE_SCENARIOS, "ar_20221231"), [0]);
	assert.equal(formatSlrOptionLabel(3), "+3 ft SLR (~2080–2100)");
});

test("hasDeltaForSelection and resolveScenario handle depth, delta, and fallback", () => {
	assert.equal(hasDeltaForSelection(SAMPLE_SCENARIOS, "100yr", 0), false);
	assert.equal(hasDeltaForSelection(SAMPLE_SCENARIOS, "100yr", 3), true);
	assert.equal(hasDeltaForSelection(SAMPLE_SCENARIOS, "ar_20221231", 0), false);

	const deltaPick = resolveScenario(SAMPLE_SCENARIOS, {
		storm: "100yr",
		slrFt: 3,
		metric: "delta",
	});
	assert.equal(deltaPick?.id, "sf_citywide_100yr_slr3ft_delta");
	assert.equal(formatOpcPathway(deltaPick), "2024 CA OPC Intermediate (~2100)");

	// Auto-falls back to depth when delta is unavailable at 0 ft or on AR hindcast
	const fallbackZero = resolveScenario(SAMPLE_SCENARIOS, {
		storm: "100yr",
		slrFt: 0,
		metric: "delta",
	});
	assert.equal(fallbackZero?.id, "sf_citywide_100yr_slr0ft_depth");

	const fallbackAr = resolveScenario(SAMPLE_SCENARIOS, {
		storm: "ar_20221231",
		slrFt: 3,
		metric: "delta",
	});
	assert.equal(fallbackAr?.id, "sf_citywide_ar_20221231_depth");
});
