import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
	cogProtocol,
	locationValues,
	setColorFunction,
} from "@geomatico/maplibre-cog-protocol";
import {
	createFloodColorRamp,
	formatDepth,
	getLegendBins,
	isDryOrNoData,
} from "./floodColors.js";
import "./style.css";

const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const MANIFEST_URL = `${window.location.origin}/data/manifest.json`;
const BENCHMARK_GEOJSON_URL = `${window.location.origin}/data/sf_sfpuc_100yr_benchmark.geojson`;

const FLOOD_SOURCE_ID = "flood-cog";
const FLOOD_LAYER_ID = "flood-depth-layer";
const BENCHMARK_SOURCE_ID = "sfpuc-benchmark";
const BENCHMARK_FILL_LAYER_ID = "sfpuc-benchmark-fill";
const BENCHMARK_LINE_LAYER_ID = "sfpuc-benchmark-line";

const scenarioSelect = document.getElementById("scenario-select");
const opacitySlider = document.getElementById("opacity-slider");
const opacityValueEl = document.getElementById("opacity-value");
const benchmarkToggle = document.getElementById("benchmark-toggle");
const legendTitleEl = document.getElementById("legend-title");
const legendListEl = document.getElementById("legend-list");
const statusTextEl = document.getElementById("status-text");

let scenariosById = new Map();
let activeScenario = null;
let activeCogUrl = null;
let currentOpacity = Number(opacitySlider.value) / 100;
let mapLoaded = false;

maplibregl.addProtocol("cog", cogProtocol);

const map = new maplibregl.Map({
	container: "map",
	style: BASEMAP_STYLE_URL,
	center: [-122.408, 37.75],
	zoom: 12.8,
});

map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(
	new maplibregl.ScaleControl({ unit: "imperial" }),
	"bottom-right",
);

const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true });

function resolveDataUrl(urlOrPath) {
	if (/^https?:\/\//i.test(urlOrPath)) {
		return urlOrPath;
	}
	const normalized = urlOrPath.startsWith("/") ? urlOrPath : `/${urlOrPath}`;
	return `${window.location.origin}${normalized}`;
}

function escapeHtml(text) {
	return String(text)
		.replaceAll("&", "&amp;")
		.replaceAll("<", "&lt;")
		.replaceAll(">", "&gt;")
		.replaceAll('"', "&quot;")
		.replaceAll("'", "&#39;");
}

function findFirstLabelLayerId(mapInstance) {
	const layers = mapInstance.getStyle()?.layers ?? [];
	const labelLayer = layers.find(
		(layer) => layer.type === "symbol" && layer.layout?.["text-field"],
	);
	return labelLayer?.id;
}

function renderLegend(metric = "depth") {
	legendTitleEl.textContent =
		metric === "delta"
			? "Added SLR Flood Depth (Delta)"
			: "Max Flood Depth (Peak)";
	const bins = getLegendBins(metric);
	legendListEl.replaceChildren();

	for (const bin of bins) {
		const item = document.createElement("li");
		item.className = "legend-item";

		const swatch = document.createElement("span");
		swatch.className = "legend-swatch";
		swatch.style.backgroundColor = bin.color;

		const label = document.createElement("span");
		label.textContent = bin.label;

		const note = document.createElement("span");
		note.className = "legend-note";
		note.textContent = bin.note;

		item.append(swatch, label, note);
		legendListEl.append(item);
	}
}

function setStatus(message, isError = false) {
	statusTextEl.textContent = message;
	statusTextEl.classList.toggle("error", isError);
}

function applyScenario(scenario) {
	if (!scenario || !mapLoaded) {
		return;
	}

	const cogUrl = resolveDataUrl(scenario.url);
	setColorFunction(cogUrl, createFloodColorRamp(scenario.metric));

	if (map.getLayer(FLOOD_LAYER_ID)) {
		map.removeLayer(FLOOD_LAYER_ID);
	}
	if (map.getSource(FLOOD_SOURCE_ID)) {
		map.removeSource(FLOOD_SOURCE_ID);
	}

	map.addSource(FLOOD_SOURCE_ID, {
		type: "raster",
		url: `cog://${cogUrl}`,
		tileSize: 256,
	});

	const beforeId = map.getLayer(BENCHMARK_FILL_LAYER_ID)
		? BENCHMARK_FILL_LAYER_ID
		: findFirstLabelLayerId(map);

	map.addLayer(
		{
			id: FLOOD_LAYER_ID,
			type: "raster",
			source: FLOOD_SOURCE_ID,
			paint: {
				"raster-opacity": currentOpacity,
			},
		},
		beforeId,
	);

	activeScenario = scenario;
	activeCogUrl = cogUrl;
	renderLegend(scenario.metric);
	setStatus("Click any flooded cell to query depth.");
}

async function initBenchmarkOverlay(beforeId) {
	try {
		const response = await fetch(BENCHMARK_GEOJSON_URL, { cache: "no-store" });
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}`);
		}
		const geojson = await response.json();

		map.addSource(BENCHMARK_SOURCE_ID, {
			type: "geojson",
			data: geojson,
		});

		const visibility = benchmarkToggle.checked ? "visible" : "none";
		map.addLayer(
			{
				id: BENCHMARK_FILL_LAYER_ID,
				type: "fill",
				source: BENCHMARK_SOURCE_ID,
				layout: { visibility },
				paint: {
					"fill-color": "#f59e0b",
					"fill-opacity": 0.16,
				},
			},
			beforeId,
		);
		map.addLayer(
			{
				id: BENCHMARK_LINE_LAYER_ID,
				type: "line",
				source: BENCHMARK_SOURCE_ID,
				layout: { visibility },
				paint: {
					"line-color": "#f59e0b",
					"line-width": 1.6,
					"line-dasharray": [2, 1.5],
				},
			},
			beforeId,
		);
	} catch {
		benchmarkToggle.disabled = true;
		benchmarkToggle.title = "SFPUC benchmark GeoJSON not found in /data/";
	}
}

async function loadManifest() {
	const response = await fetch(MANIFEST_URL, { cache: "no-store" });
	if (!response.ok) {
		throw new Error(`HTTP ${response.status} loading manifest.json`);
	}
	const manifest = await response.json();
	const scenarios = Array.isArray(manifest?.scenarios)
		? manifest.scenarios
		: [];
	if (scenarios.length === 0) {
		throw new Error("manifest.json contains no scenarios");
	}

	scenariosById = new Map(scenarios.map((sc) => [sc.id, sc]));
	scenarioSelect.replaceChildren();
	for (const sc of scenarios) {
		const option = document.createElement("option");
		option.value = sc.id;
		option.textContent = sc.label;
		scenarioSelect.append(option);
	}
	scenarioSelect.disabled = false;
	return scenarios[0];
}

renderLegend("depth");

const manifestPromise = loadManifest().catch((err) => {
	setStatus(
		`Unable to load /data/manifest.json (${err.message}). Run pipeline/04_export_cog.py first.`,
		true,
	);
	return null;
});

map.on("load", async () => {
	mapLoaded = true;
	const beforeId = findFirstLabelLayerId(map);
	await initBenchmarkOverlay(beforeId);

	const initialScenario = await manifestPromise;
	if (initialScenario) {
		scenarioSelect.value = initialScenario.id;
		applyScenario(initialScenario);
	}
});

scenarioSelect.addEventListener("change", () => {
	const selected = scenariosById.get(scenarioSelect.value);
	if (selected) {
		popup.remove();
		applyScenario(selected);
	}
});

opacitySlider.addEventListener("input", () => {
	const pct = Number(opacitySlider.value);
	currentOpacity = pct / 100;
	opacityValueEl.textContent = `${pct}%`;
	if (mapLoaded && map.getLayer(FLOOD_LAYER_ID)) {
		map.setPaintProperty(FLOOD_LAYER_ID, "raster-opacity", currentOpacity);
	}
});

benchmarkToggle.addEventListener("change", () => {
	const visibility = benchmarkToggle.checked ? "visible" : "none";
	for (const layerId of [BENCHMARK_FILL_LAYER_ID, BENCHMARK_LINE_LAYER_ID]) {
		if (map.getLayer(layerId)) {
			map.setLayoutProperty(layerId, "visibility", visibility);
		}
	}
});

map.on("click", async (e) => {
	if (!activeCogUrl || !activeScenario) {
		return;
	}
	const { lng, lat } = e.lngLat;
	try {
		const values = await locationValues(
			activeCogUrl,
			{ latitude: lat, longitude: lng },
			Math.round(map.getZoom()),
		);
		const depth = values?.[0];
		const labelHtml = `<strong>${escapeHtml(activeScenario.label)}</strong>`;
		const bodyHtml = isDryOrNoData(depth)
			? "Dry / below 2 in (0.05 m)"
			: `Max flood depth: <strong>${formatDepth(depth)}</strong>`;

		popup
			.setLngLat(e.lngLat)
			.setHTML(`${labelHtml}<br/>${bodyHtml}`)
			.addTo(map);
	} catch {
		popup
			.setLngLat(e.lngLat)
			.setHTML(
				"Unable to query COG (ensure local assets exist in <code>data/web_outputs/</code>).",
			)
			.addTo(map);
	}
});
