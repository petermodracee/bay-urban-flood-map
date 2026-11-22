export const STORM_LABELS = {
	"100yr": "100-Yr Design Storm",
	"10yr": "10-Yr Design Storm",
	ar_20221231: "Dec 31, 2022 AR Hindcast",
};

const SLR_LABELS = {
	0: "Today (0 ft SLR)",
	1: "+1 ft SLR (~2040–2050)",
	3: "+3 ft SLR (~2080–2100)",
	5: "+5 ft SLR (~2100)",
};

export function formatSlrOptionLabel(slrFt) {
	if (Object.hasOwn(SLR_LABELS, slrFt)) {
		return SLR_LABELS[slrFt];
	}
	return slrFt === 0 ? "Today (0 ft SLR)" : `+${slrFt} ft SLR`;
}

export function getAvailableStorms(scenarios) {
	const seen = new Set();
	const storms = [];
	for (const sc of scenarios ?? []) {
		if (!sc?.storm || seen.has(sc.storm)) {
			continue;
		}
		seen.add(sc.storm);
		storms.push({
			id: sc.storm,
			label: STORM_LABELS[sc.storm] ?? sc.label ?? sc.storm,
		});
	}
	return storms;
}

export function getAvailableSlrLevels(scenarios, storm) {
	const levels = new Set();
	for (const sc of scenarios ?? []) {
		if (sc?.storm === storm && typeof sc.slr_ft === "number") {
			levels.add(sc.slr_ft);
		}
	}
	return [...levels].sort((a, b) => a - b);
}

export function hasDeltaForSelection(scenarios, storm, slrFt) {
	if (slrFt <= 0) {
		return false;
	}
	return (scenarios ?? []).some(
		(sc) => sc?.storm === storm && sc.slr_ft === slrFt && sc.metric === "delta",
	);
}

export function resolveScenario(scenarios, { storm, slrFt, metric }) {
	const list = scenarios ?? [];
	if (list.length === 0) {
		return null;
	}

	const effectiveMetric =
		metric === "delta" && hasDeltaForSelection(list, storm, slrFt)
			? "delta"
			: "depth";

	return (
		list.find(
			(sc) =>
				sc.storm === storm &&
				sc.slr_ft === slrFt &&
				sc.metric === effectiveMetric,
		) ??
		list.find(
			(sc) =>
				sc.storm === storm && sc.slr_ft === slrFt && sc.metric === "depth",
		) ??
		list.find((sc) => sc.storm === storm) ??
		list[0]
	);
}

export function formatOpcPathway(scenario) {
	return scenario?.opc_2024_pathway ?? "";
}
