export const STORM_LABELS = {
	"100yr": "100-Yr Rain + High Tide (Stress Scenario)",
	"10yr": "10-Yr Rain + High Tide (Stress Scenario)",
	ar_20211024: "Oct 24, 2021 Atmospheric River",
	ar_20221231: "Dec 31, 2022 Atmospheric River",
	ar_20230109: "Jan 9, 2023 Atmospheric River",
	ar_20241122: "Nov 22, 2024 Atmospheric River",
};

export const HOTSPOTS = [
	{ id: "alemany", label: "Alemany", lng: -122.411, lat: 37.734, zoom: 15.0 },
	{
		id: "soma",
		label: "SOMA / Mission Bay",
		lng: -122.398,
		lat: 37.775,
		zoom: 14.8,
	},
	{
		id: "bayview",
		label: "Bayview / Islais",
		lng: -122.393,
		lat: 37.743,
		zoom: 14.5,
	},
	{
		id: "sunset",
		label: "Sunset / Wawona",
		lng: -122.495,
		lat: 37.737,
		zoom: 15.0,
	},
];

export function isHistoricalStorm(stormId) {
	return String(stormId ?? "").startsWith("ar_");
}

export function groupAvailableStorms(scenarios) {
	const storms = getAvailableStorms(scenarios);
	return {
		design: storms.filter((st) => !isHistoricalStorm(st.id)),
		historical: storms.filter((st) => isHistoricalStorm(st.id)),
	};
}

export function formatManifestSubtitle(manifest) {
	const rawVersion = String(manifest?.version ?? "").trim();
	const version = rawVersion
		? rawVersion.startsWith("v")
			? rawVersion
			: `v${rawVersion}`
		: "v0.6";
	const parts = ["2 m Subgrid"];
	if (manifest?.drainage_tier) {
		parts.push(manifest.drainage_tier);
	}
	if (manifest?.rain_source) {
		parts.push(manifest.rain_source);
	}
	return `${version} San Francisco (${parts.join(" · ")})`;
}

export function parseUrlHash(hashString) {
	const raw = String(hashString ?? "")
		.replace(/^#/, "")
		.trim();
	if (!raw) {
		return {};
	}
	const params = new URLSearchParams(raw);
	const parsed = {};
	const storm = params.get("storm");
	if (storm) {
		parsed.storm = storm;
	}
	const slrRaw = params.get("slr");
	if (slrRaw !== null && slrRaw !== "" && Number.isFinite(Number(slrRaw))) {
		parsed.slrFt = Number(slrRaw);
	}
	const metric = params.get("metric");
	if (metric === "depth" || metric === "delta") {
		parsed.metric = metric;
	}
	const latRaw = params.get("lat");
	const lngRaw = params.get("lng");
	const zRaw = params.get("z");
	if (latRaw !== null && lngRaw !== null) {
		const lat = Number(latRaw);
		const lng = Number(lngRaw);
		if (
			Number.isFinite(lat) &&
			Number.isFinite(lng) &&
			lat >= -90 &&
			lat <= 90 &&
			lng >= -180 &&
			lng <= 180
		) {
			parsed.lat = lat;
			parsed.lng = lng;
		}
	}
	if (zRaw !== null && zRaw !== "" && Number.isFinite(Number(zRaw))) {
		parsed.zoom = Number(zRaw);
	}
	if (params.get("3d") === "1") {
		parsed.terrain3d = true;
	}
	return parsed;
}

export function formatUrlHash({
	storm,
	slrFt,
	metric,
	lat,
	lng,
	zoom,
	terrain3d,
}) {
	const params = new URLSearchParams();
	if (storm) {
		params.set("storm", storm);
	}
	if (typeof slrFt === "number" && Number.isFinite(slrFt)) {
		params.set("slr", String(slrFt));
	}
	if (metric) {
		params.set("metric", metric);
	}
	if (typeof lat === "number" && Number.isFinite(lat)) {
		params.set("lat", lat.toFixed(4));
	}
	if (typeof lng === "number" && Number.isFinite(lng)) {
		params.set("lng", lng.toFixed(4));
	}
	if (typeof zoom === "number" && Number.isFinite(zoom)) {
		params.set("z", zoom.toFixed(2));
	}
	if (terrain3d) {
		params.set("3d", "1");
	}
	return `#${params.toString()}`;
}

export function buildGeocoderUrl(query) {
	const q = String(query ?? "").trim();
	const params = new URLSearchParams({
		q,
		bbox: "-122.53,37.70,-122.35,37.83",
		limit: "5",
	});
	return `https://photon.komoot.io/api/?${params.toString()}`;
}

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
