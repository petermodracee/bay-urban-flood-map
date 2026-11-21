export const MIN_FLOOD_DEPTH_M = 0.05;
export const DEFAULT_NODATA = -9999;

const DEPTH_STOPS = [
	{ depth: 0.05, color: [186, 230, 253, 150] },
	{ depth: 0.15, color: [56, 189, 248, 190] },
	{ depth: 0.3, color: [37, 99, 235, 215] },
	{ depth: 0.91, color: [30, 58, 138, 232] },
	{ depth: 2.0, color: [88, 28, 135, 245] },
];

const DELTA_STOPS = [
	{ depth: 0.05, color: [253, 230, 138, 155] },
	{ depth: 0.15, color: [245, 158, 11, 195] },
	{ depth: 0.3, color: [234, 88, 12, 220] },
	{ depth: 0.91, color: [185, 28, 28, 240] },
	{ depth: 2.0, color: [127, 29, 29, 250] },
];

const COLOR_RAMPS = {
	depth: DEPTH_STOPS,
	delta: DELTA_STOPS,
};

export const DEPTH_LEGEND_BINS = [
	{ label: "2–6 in (0.05–0.15 m)", note: "Nuisance ponding", color: "#bae6fd" },
	{
		label: "6–12 in (0.15–0.30 m)",
		note: "Curb / SFPUC threshold",
		color: "#38bdf8",
	},
	{
		label: "1–3 ft (0.30–0.91 m)",
		note: "Impassable to cars",
		color: "#2563eb",
	},
	{ label: "> 3 ft (> 0.91 m)", note: "Severe inundation", color: "#581c87" },
];

export const DELTA_LEGEND_BINS = [
	{
		label: "2–6 in (0.05–0.15 m)",
		note: "Minor SLR increase",
		color: "#fde68a",
	},
	{
		label: "6–12 in (0.15–0.30 m)",
		note: "Moderate added ponding",
		color: "#f59e0b",
	},
	{
		label: "1–3 ft (0.30–0.91 m)",
		note: "Major drainage lock",
		color: "#ea580c",
	},
	{ label: "> 3 ft (> 0.91 m)", note: "Severe SLR delta", color: "#b91c1c" },
];

export function isDryOrNoData(
	value,
	minDepthM = MIN_FLOOD_DEPTH_M,
	noData = DEFAULT_NODATA,
) {
	return (
		value === undefined ||
		value === null ||
		Number.isNaN(value) ||
		value === noData ||
		value === DEFAULT_NODATA ||
		value < minDepthM
	);
}

export function formatDepth(depthM) {
	const inches = depthM * 39.3701;
	const imperial =
		inches < 12 ? `${inches.toFixed(1)} in` : `${(inches / 12).toFixed(2)} ft`;
	return `${imperial} (${depthM.toFixed(2)} m)`;
}

export function createFloodColorRamp(metric = "depth") {
	const stops = COLOR_RAMPS[metric] ?? DEPTH_STOPS;

	return function floodColorRamp(pixel, color, metadata) {
		const depth = pixel[0];
		if (isDryOrNoData(depth, MIN_FLOOD_DEPTH_M, metadata?.noData)) {
			color.set([0, 0, 0, 0]);
			return;
		}

		const lastStop = stops[stops.length - 1];
		if (depth >= lastStop.depth) {
			color.set(lastStop.color);
			return;
		}

		for (let i = 0; i < stops.length - 1; i++) {
			const a = stops[i];
			const b = stops[i + 1];
			if (depth >= a.depth && depth <= b.depth) {
				const t = (depth - a.depth) / (b.depth - a.depth);
				color[0] = Math.round(a.color[0] + t * (b.color[0] - a.color[0]));
				color[1] = Math.round(a.color[1] + t * (b.color[1] - a.color[1]));
				color[2] = Math.round(a.color[2] + t * (b.color[2] - a.color[2]));
				color[3] = Math.round(a.color[3] + t * (b.color[3] - a.color[3]));
				return;
			}
		}
	};
}

export function getLegendBins(metric = "depth") {
	return metric === "delta" ? DELTA_LEGEND_BINS : DEPTH_LEGEND_BINS;
}
