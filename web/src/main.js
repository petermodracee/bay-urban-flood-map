import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
	cogProtocol,
	locationValues,
	setColorFunction,
} from "@geomatico/maplibre-cog-protocol";
import { resolveDataAssetUrl } from "./dataUrls.js";
import {
	createFloodColorRamp,
	formatDepth,
	getLegendBins,
	isDryOrNoData,
} from "./floodColors.js";
import {
	buildGeocoderUrl,
	formatManifestSubtitle,
	formatOpcPathway,
	formatSlrOptionLabel,
	formatUrlHash,
	getAvailableSlrLevels,
	groupAvailableStorms,
	HOTSPOTS,
	hasDeltaForSelection,
	isHistoricalStorm,
	parseUrlHash,
	resolveScenario,
} from "./scenarioControls.js";
import "./style.css";

const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const TERRARIUM_TILE_URL =
	"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
const DATA_BASE_URL = import.meta.env.VITE_DATA_BASE_URL;
const MANIFEST_URL = resolveDataAssetUrl(
	"manifest.json",
	DATA_BASE_URL,
	window.location.origin,
);

const TERRAIN_SOURCE_ID = "aws-terrarium";
const TERRAIN_HILLSHADE_LAYER_ID = "basemap-terrain-hillshade";
const TOPO_CONTOURS_SOURCE_ID = "sf-topo-contours";
const TOPO_CONTOURS_LINE_LAYER_ID = "sf-topo-contours-line";
const TOPO_CONTOURS_LABEL_LAYER_ID = "sf-topo-contours-label";
const BUILDINGS_3D_LAYER_ID = "basemap-buildings-3d";
const HIGHWAYS_OVERLAY_LAYER_ID = "basemap-highways-overlay";
const BRIDGES_SHADOW_LAYER_ID = "basemap-bridges-shadow";
const BRIDGES_CASING_LAYER_ID = "basemap-bridges-casing";
const BRIDGES_DECK_LAYER_ID = "basemap-bridges-deck";
const FLOOD_SOURCE_ID = "flood-cog";
const FLOOD_LAYER_ID = "flood-depth-layer";
const BENCHMARK_SOURCE_ID = "sfpuc-benchmark";
const BENCHMARK_FILL_LAYER_ID = "sfpuc-benchmark-fill";
const BENCHMARK_LINE_LAYER_ID = "sfpuc-benchmark-line";
const FLOODS_311_SOURCE_ID = "sf-311-floods";
const FLOODS_311_LAYER_ID = "sf-311-floods-circle";
const TIER_C_SOURCE_ID = "sf-tier-c-closures";
const TIER_C_LAYER_ID = "sf-tier-c-closures-circle";
const TIER_A_SOURCE_ID = "sf-tier-a-gauges";
const TIER_A_LAYER_ID = "sf-tier-a-gauges-circle";
const TIER_E_SOURCE_ID = "sf-tier-e-nfip";
const TIER_E_FILL_LAYER_ID = "sf-tier-e-nfip-fill";
const TIER_E_LINE_LAYER_ID = "sf-tier-e-nfip-line";

const panelSubtitleEl = document.getElementById("panel-subtitle");
const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const searchResultsEl = document.getElementById("search-results");
const hotspotChipsEl = document.getElementById("hotspot-chips");
const stormSelect = document.getElementById("storm-select");
const slrSelect = document.getElementById("slr-select");
const opcPathwayEl = document.getElementById("opc-pathway-text");
const metricDepthBtn = document.getElementById("metric-depth-btn");
const metricDeltaBtn = document.getElementById("metric-delta-btn");
const opacitySlider = document.getElementById("opacity-slider");
const opacityValueEl = document.getElementById("opacity-value");
let terrain3dToggle = null;
const elevatedBridgesToggle = document.getElementById(
	"elevated-bridges-toggle",
);
const benchmarkToggle = document.getElementById("benchmark-toggle");
const floods311Toggle = document.getElementById("floods-311-toggle");
const floods311Label = document.getElementById("floods-311-label");
const tierCToggle = document.getElementById("tier-c-toggle");
const tierCLabel = document.getElementById("tier-c-label");
const tierAToggle = document.getElementById("tier-a-toggle");
const tierEToggle = document.getElementById("tier-e-toggle");
const tierELabel = document.getElementById("tier-e-label");
const nfipLegendEl = document.getElementById("nfip-legend");
const legendTitleEl = document.getElementById("legend-title");
const legendListEl = document.getElementById("legend-list");
const highwayLegendEl = document.getElementById("highway-legend");
const statusTextEl = document.getElementById("status-text");

const initialHash = parseUrlHash(window.location.hash);

let allScenarios = [];
let manifestOverlays = {};
let selectedStorm = initialHash.storm ?? null;
let selectedSlrFt = initialHash.slrFt ?? 0;
let selectedMetric = initialHash.metric ?? "depth";
let activeScenario = null;
let activeCogUrl = null;
let currentOpacity = Number(opacitySlider.value) / 100;
let mapLoaded = false;
let tierAGeoJsonCache = null;
const geojsonCache = new Map();

maplibregl.addProtocol("cog", cogProtocol);

let activeDemCogUrl = null;
const SF_DEM_BOUNDS = {
	minLng: -122.53,
	maxLng: -122.35,
	minLat: 37.703,
	maxLat: 37.82,
};

function tileOverlapsSfBounds(z, x, y) {
	const n = 2 ** z;
	const minLng = (x / n) * 360 - 180;
	const maxLng = ((x + 1) / n) * 360 - 180;
	const toLat = (ty) =>
		(Math.atan(Math.sinh(Math.PI * (1 - (2 * ty) / n))) * 180) / Math.PI;
	const maxLat = toLat(y);
	const minLat = toLat(y + 1);
	return (
		maxLng >= SF_DEM_BOUNDS.minLng &&
		minLng <= SF_DEM_BOUNDS.maxLng &&
		maxLat >= SF_DEM_BOUNDS.minLat &&
		minLat <= SF_DEM_BOUNDS.maxLat
	);
}

maplibregl.addProtocol("hybrid-dem", async (params, abortController) => {
	const match = params.url.match(/hybrid-dem:\/\/(\d+)\/(\d+)\/(\d+)/);
	if (!match) {
		throw new Error(`Invalid hybrid-dem URL: ${params.url}`);
	}
	const z = Number(match[1]);
	const x = Number(match[2]);
	const y = Number(match[3]);

	const awsUrl = TERRARIUM_TILE_URL.replace("{z}", String(z))
		.replace("{x}", String(x))
		.replace("{y}", String(y));
	const awsResp = await fetch(awsUrl, { signal: abortController.signal });
	if (!awsResp.ok) {
		throw new Error(`Terrarium fetch failed (${awsResp.status})`);
	}
	const awsBitmap = await createImageBitmap(await awsResp.blob());

	if (!activeDemCogUrl || z > 15 || !tileOverlapsSfBounds(z, x, y)) {
		return { data: awsBitmap };
	}

	try {
		const cogRes = await cogProtocol(
			{ type: "image", url: `cog://${activeDemCogUrl}/${z}/${x}/${y}` },
			abortController,
		);
		if (!cogRes?.data) {
			return { data: awsBitmap };
		}
		const canvas =
			typeof OffscreenCanvas !== "undefined"
				? new OffscreenCanvas(256, 256)
				: Object.assign(document.createElement("canvas"), {
						width: 256,
						height: 256,
					});
		const ctx = canvas.getContext("2d");
		ctx.drawImage(awsBitmap, 0, 0, 256, 256);
		const baseImg = ctx.getImageData(0, 0, 256, 256);

		const cogCanvas =
			typeof OffscreenCanvas !== "undefined"
				? new OffscreenCanvas(256, 256)
				: Object.assign(document.createElement("canvas"), {
						width: 256,
						height: 256,
					});
		const cogCtx = cogCanvas.getContext("2d");
		cogCtx.drawImage(cogRes.data, 0, 0, 256, 256);
		const cogImg = cogCtx.getImageData(0, 0, 256, 256);

		const dst = baseImg.data;
		const src = cogImg.data;
		for (let i = 0; i < dst.length; i += 4) {
			// Only replace with LiDAR Terrarium on fully opaque pixels so alpha blending never corrupts RGB elevation bytes
			if (src[i + 3] === 255) {
				dst[i] = src[i];
				dst[i + 1] = src[i + 1];
				dst[i + 2] = src[i + 2];
				dst[i + 3] = 255;
			}
		}
		ctx.putImageData(baseImg, 0, 0);
		const composite =
			typeof canvas.transferToImageBitmap === "function"
				? canvas.transferToImageBitmap()
				: await createImageBitmap(canvas);
		return { data: composite };
	} catch {
		return { data: awsBitmap };
	}
});

const map = new maplibregl.Map({
	container: "map",
	style: BASEMAP_STYLE_URL,
	center: [initialHash.lng ?? -122.445, initialHash.lat ?? 37.76],
	zoom: initialHash.zoom ?? 12.0,
	maxPitch: initialHash.terrain3d ? 70 : 0,
});

map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(
	{
		onAdd() {
			const container = document.createElement("div");
			container.className =
				"maplibregl-ctrl maplibregl-ctrl-group map-terrain-ctrl";
			const label = document.createElement("label");
			label.className = "map-terrain-toggle";
			label.htmlFor = "terrain-3d-toggle";
			label.title = "Toggle 3D terrain, topo contours, and 3D buildings";

			const checkbox = document.createElement("input");
			checkbox.id = "terrain-3d-toggle";
			checkbox.type = "checkbox";
			checkbox.checked = Boolean(initialHash.terrain3d);
			checkbox.addEventListener("change", () => {
				applyTerrain3d(checkbox.checked);
			});

			const text = document.createElement("span");
			text.textContent = "3D";

			label.append(checkbox, text);
			container.append(label);
			terrain3dToggle = checkbox;
			return container;
		},
		onRemove() {
			terrain3dToggle = null;
		},
	},
	"top-right",
);
map.addControl(
	new maplibregl.ScaleControl({ unit: "imperial" }),
	"bottom-right",
);

const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: false });

function resolveDataUrl(urlOrPath) {
	return resolveDataAssetUrl(urlOrPath, DATA_BASE_URL, window.location.origin);
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

function findBasemapVectorSourceId(mapInstance) {
	const sources = mapInstance.getStyle()?.sources ?? {};
	const match = Object.entries(sources).find(
		([, source]) => source?.type === "vector",
	);
	return match?.[0] ?? null;
}

function ensureContextOverlayLayers(mapInstance, beforeId) {
	const vectorSourceId = findBasemapVectorSourceId(mapInstance);
	if (!vectorSourceId) {
		return;
	}

	if (!mapInstance.getLayer(HIGHWAYS_OVERLAY_LAYER_ID)) {
		mapInstance.addLayer(
			{
				id: HIGHWAYS_OVERLAY_LAYER_ID,
				type: "line",
				source: vectorSourceId,
				"source-layer": "transportation",
				filter: [
					"all",
					[
						"in",
						["get", "class"],
						[
							"literal",
							["motorway", "trunk", "primary", "secondary", "tertiary"],
						],
					],
					["!=", ["get", "brunnel"], "bridge"],
				],
				layout: {
					"line-cap": "round",
					"line-join": "round",
				},
				paint: {
					"line-color": [
						"case",
						["==", ["get", "brunnel"], "tunnel"],
						"#64748b",
						[
							"match",
							["get", "class"],
							"motorway",
							"#475569",
							"trunk",
							"#64748b",
							"#94a3b8",
						],
					],
					"line-width": [
						"interpolate",
						["linear"],
						["zoom"],
						10,
						["match", ["get", "class"], "motorway", 1.5, 0.75],
						14,
						["match", ["get", "class"], "motorway", 4, "trunk", 3, 1.8],
						17,
						["match", ["get", "class"], "motorway", 7, "trunk", 5.5, 3.2],
					],
					"line-opacity": 0.65,
				},
			},
			beforeId,
		);
	}

	if (!mapInstance.getLayer(BRIDGES_SHADOW_LAYER_ID)) {
		mapInstance.addLayer(
			{
				id: BRIDGES_SHADOW_LAYER_ID,
				type: "line",
				source: vectorSourceId,
				"source-layer": "transportation",
				filter: [
					"all",
					[
						"in",
						["get", "class"],
						[
							"literal",
							["motorway", "trunk", "primary", "secondary", "tertiary"],
						],
					],
					["==", ["get", "brunnel"], "bridge"],
				],
				layout: {
					"line-cap": "round",
					"line-join": "round",
				},
				paint: {
					"line-color": "#0f172a",
					"line-width": [
						"interpolate",
						["linear"],
						["zoom"],
						10,
						["match", ["get", "class"], "motorway", 3.5, 2],
						14,
						["match", ["get", "class"], "motorway", 8.5, "trunk", 7, 4.5],
						17,
						["match", ["get", "class"], "motorway", 13.5, "trunk", 11.5, 7.5],
					],
					"line-opacity": 0.24,
					"line-blur": 2.5,
					"line-translate": [1.5, 2.5],
					"line-translate-anchor": "viewport",
				},
			},
			beforeId,
		);
	}

	if (!mapInstance.getLayer(BRIDGES_CASING_LAYER_ID)) {
		mapInstance.addLayer(
			{
				id: BRIDGES_CASING_LAYER_ID,
				type: "line",
				source: vectorSourceId,
				"source-layer": "transportation",
				filter: [
					"all",
					[
						"in",
						["get", "class"],
						[
							"literal",
							["motorway", "trunk", "primary", "secondary", "tertiary"],
						],
					],
					["==", ["get", "brunnel"], "bridge"],
				],
				layout: {
					"line-cap": "butt",
					"line-join": "round",
				},
				paint: {
					"line-color": "#334155",
					"line-width": [
						"interpolate",
						["linear"],
						["zoom"],
						10,
						["match", ["get", "class"], "motorway", 3, 1.8],
						14,
						["match", ["get", "class"], "motorway", 7, "trunk", 5.8, 3.8],
						17,
						["match", ["get", "class"], "motorway", 11, "trunk", 9.5, 6.2],
					],
					"line-opacity": 0.9,
					"line-translate": [0, 0],
					"line-translate-anchor": "viewport",
				},
			},
			beforeId,
		);
	}

	if (!mapInstance.getLayer(BRIDGES_DECK_LAYER_ID)) {
		mapInstance.addLayer(
			{
				id: BRIDGES_DECK_LAYER_ID,
				type: "line",
				source: vectorSourceId,
				"source-layer": "transportation",
				filter: [
					"all",
					[
						"in",
						["get", "class"],
						[
							"literal",
							["motorway", "trunk", "primary", "secondary", "tertiary"],
						],
					],
					["==", ["get", "brunnel"], "bridge"],
				],
				layout: {
					"line-cap": "round",
					"line-join": "round",
				},
				paint: {
					"line-color": [
						"match",
						["get", "class"],
						"motorway",
						"#f59e0b",
						"trunk",
						"#fbbf24",
						"#e2e8f0",
					],
					"line-width": [
						"interpolate",
						["linear"],
						["zoom"],
						10,
						["match", ["get", "class"], "motorway", 2, 1],
						14,
						["match", ["get", "class"], "motorway", 5, "trunk", 4, 2.5],
						17,
						["match", ["get", "class"], "motorway", 8.5, "trunk", 7, 4.5],
					],
					"line-opacity": 0.9,
					"line-translate": [0, 0],
					"line-translate-anchor": "viewport",
				},
			},
			beforeId,
		);
	}

	if (!mapInstance.getLayer(BUILDINGS_3D_LAYER_ID)) {
		mapInstance.addLayer(
			{
				id: BUILDINGS_3D_LAYER_ID,
				type: "fill-extrusion",
				source: vectorSourceId,
				"source-layer": "building",
				minzoom: 13,
				layout: {
					visibility: "none",
				},
				paint: {
					"fill-extrusion-color": "#cbd5e1",
					"fill-extrusion-height": [
						"coalesce",
						["get", "render_height"],
						["get", "height"],
						6,
					],
					"fill-extrusion-base": [
						"coalesce",
						["get", "render_min_height"],
						["get", "min_height"],
						0,
					],
					"fill-extrusion-opacity": 0.72,
				},
			},
			beforeId,
		);
	}
}

function syncUrlHash() {
	if (!selectedStorm) {
		return;
	}
	const center = map.getCenter();
	const nextHash = formatUrlHash({
		storm: selectedStorm,
		slrFt: selectedSlrFt,
		metric: selectedMetric,
		lat: center.lat,
		lng: center.lng,
		zoom: map.getZoom(),
		terrain3d: Boolean(terrain3dToggle?.checked),
	});
	window.history.replaceState(null, "", nextHash);
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

async function fetchGeoJsonCached(urlOrPath) {
	const url = resolveDataUrl(urlOrPath);
	if (geojsonCache.has(url)) {
		return geojsonCache.get(url);
	}
	const response = await fetch(url, { cache: "no-store" });
	if (!response.ok) {
		throw new Error(`HTTP ${response.status}`);
	}
	const data = await response.json();
	geojsonCache.set(url, data);
	return data;
}

function syncControlsFromState() {
	const slrLevels = getAvailableSlrLevels(allScenarios, selectedStorm);
	if (!slrLevels.includes(selectedSlrFt)) {
		selectedSlrFt = slrLevels[0] ?? 0;
	}

	slrSelect.replaceChildren();
	for (const ft of slrLevels) {
		const opt = document.createElement("option");
		opt.value = String(ft);
		opt.textContent = formatSlrOptionLabel(ft);
		slrSelect.append(opt);
	}
	slrSelect.value = String(selectedSlrFt);
	slrSelect.disabled =
		isHistoricalStorm(selectedStorm) || slrLevels.length <= 1;

	const deltaAvailable = hasDeltaForSelection(
		allScenarios,
		selectedStorm,
		selectedSlrFt,
	);
	if (!deltaAvailable && selectedMetric === "delta") {
		selectedMetric = "depth";
	}
	metricDeltaBtn.disabled = !deltaAvailable;
	metricDepthBtn.classList.toggle("active", selectedMetric === "depth");
	metricDeltaBtn.classList.toggle("active", selectedMetric === "delta");

	const resolved = resolveScenario(allScenarios, {
		storm: selectedStorm,
		slrFt: selectedSlrFt,
		metric: selectedMetric,
	});
	opcPathwayEl.textContent = formatOpcPathway(resolved);
	return resolved;
}

function applyElevatedBridgesVisibility(visible) {
	if (highwayLegendEl) {
		highwayLegendEl.hidden = !visible;
	}
	if (!mapLoaded) {
		return;
	}
	const visibility = visible ? "visible" : "none";
	for (const layerId of [
		HIGHWAYS_OVERLAY_LAYER_ID,
		BRIDGES_SHADOW_LAYER_ID,
		BRIDGES_CASING_LAYER_ID,
		BRIDGES_DECK_LAYER_ID,
	]) {
		if (map.getLayer(layerId)) {
			map.setLayoutProperty(layerId, "visibility", visibility);
		}
	}
}

function demToTerrariumRgba(pixel, rgba) {
	const raw = pixel?.[0];
	if (!Number.isFinite(raw) || raw < 0 || raw >= 9000) {
		rgba[0] = 0;
		rgba[1] = 0;
		rgba[2] = 0;
		rgba[3] = 0;
		return;
	}
	const v = raw + 32768.0;
	const r = Math.floor(v / 256);
	const g = Math.floor(v) % 256;
	const b = Math.floor((v - Math.floor(v)) * 256);
	rgba[0] = r;
	rgba[1] = g;
	rgba[2] = b;
	rgba[3] = 255;
}

function applyTerrain3d(enabled) {
	if (!mapLoaded) {
		return;
	}
	const visibility3d = enabled ? "visible" : "none";
	if (enabled) {
		if (!map.getSource(TERRAIN_SOURCE_ID)) {
			const demEntry = manifestOverlays?.dem_cog;
			if (demEntry?.url) {
				activeDemCogUrl = resolveDataUrl(demEntry.url);
				setColorFunction(activeDemCogUrl, demToTerrariumRgba);
				map.addSource(TERRAIN_SOURCE_ID, {
					type: "raster-dem",
					tiles: ["hybrid-dem://{z}/{x}/{y}"],
					encoding: "terrarium",
					tileSize: 256,
					maxzoom: 15,
				});
			} else {
				map.addSource(TERRAIN_SOURCE_ID, {
					type: "raster-dem",
					tiles: [TERRARIUM_TILE_URL],
					encoding: "terrarium",
					tileSize: 256,
					maxzoom: 15,
				});
			}
		}
		map.setMaxPitch(70);
		map.setTerrain({ source: TERRAIN_SOURCE_ID, exaggeration: 1.5 });
		if (!map.getLayer(TERRAIN_HILLSHADE_LAYER_ID)) {
			const beforeFloodId = map.getLayer(FLOOD_LAYER_ID)
				? FLOOD_LAYER_ID
				: findFirstLabelLayerId(map);
			map.addLayer(
				{
					id: TERRAIN_HILLSHADE_LAYER_ID,
					type: "hillshade",
					source: TERRAIN_SOURCE_ID,
					paint: {
						"hillshade-exaggeration": 0.45,
						"hillshade-shadow-color": "#334155",
						"hillshade-highlight-color": "#ffffff",
						"hillshade-accent-color": "#475569",
						"hillshade-illumination-direction": 315,
					},
				},
				beforeFloodId,
			);
		} else {
			map.setLayoutProperty(
				TERRAIN_HILLSHADE_LAYER_ID,
				"visibility",
				"visible",
			);
		}
		map.setLight({
			anchor: "viewport",
			color: "#ffffff",
			intensity: 0.45,
			position: [1.5, 210, 35],
		});
		if (map.getLayer(BUILDINGS_3D_LAYER_ID)) {
			map.setLayoutProperty(BUILDINGS_3D_LAYER_ID, "visibility", visibility3d);
		}
		for (const layerId of [
			TOPO_CONTOURS_LINE_LAYER_ID,
			TOPO_CONTOURS_LABEL_LAYER_ID,
		]) {
			if (map.getLayer(layerId)) {
				map.setLayoutProperty(layerId, "visibility", visibility3d);
			}
		}
		if (map.getLayer(BRIDGES_CASING_LAYER_ID)) {
			map.setPaintProperty(BRIDGES_CASING_LAYER_ID, "line-translate", [0, -6]);
		}
		if (map.getLayer(BRIDGES_DECK_LAYER_ID)) {
			map.setPaintProperty(BRIDGES_DECK_LAYER_ID, "line-translate", [0, -6]);
		}
		map.easeTo({ pitch: 55, duration: 500 });
	} else {
		map.setTerrain(null);
		map.setMaxPitch(0);
		if (map.getLayer(TERRAIN_HILLSHADE_LAYER_ID)) {
			map.setLayoutProperty(TERRAIN_HILLSHADE_LAYER_ID, "visibility", "none");
		}
		map.setLight({
			anchor: "viewport",
			color: "#ffffff",
			intensity: 0.15,
			position: [1.15, 210, 30],
		});
		if (map.getLayer(BUILDINGS_3D_LAYER_ID)) {
			map.setLayoutProperty(BUILDINGS_3D_LAYER_ID, "visibility", visibility3d);
		}
		for (const layerId of [
			TOPO_CONTOURS_LINE_LAYER_ID,
			TOPO_CONTOURS_LABEL_LAYER_ID,
		]) {
			if (map.getLayer(layerId)) {
				map.setLayoutProperty(layerId, "visibility", visibility3d);
			}
		}
		if (map.getLayer(BRIDGES_CASING_LAYER_ID)) {
			map.setPaintProperty(BRIDGES_CASING_LAYER_ID, "line-translate", [0, 0]);
		}
		if (map.getLayer(BRIDGES_DECK_LAYER_ID)) {
			map.setPaintProperty(BRIDGES_DECK_LAYER_ID, "line-translate", [0, 0]);
		}
		map.easeTo({ pitch: 0, duration: 500 });
	}
	syncUrlHash();
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

	const beforeId = map.getLayer(HIGHWAYS_OVERLAY_LAYER_ID)
		? HIGHWAYS_OVERLAY_LAYER_ID
		: map.getLayer(BENCHMARK_FILL_LAYER_ID)
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

async function syncStormValidationOverlays() {
	if (!mapLoaded) {
		return;
	}
	const historical = isHistoricalStorm(selectedStorm);

	// Tier D: 311 Flood Reports
	const entry311 = historical
		? manifestOverlays?.tier_d_by_storm?.[selectedStorm]
		: null;
	if (entry311?.url) {
		floods311Toggle.disabled = false;
		floods311Label.textContent = `311 Reports (${entry311.count ?? "?"})`;
		try {
			const data = await fetchGeoJsonCached(entry311.url);
			const src = map.getSource(FLOODS_311_SOURCE_ID);
			if (src) {
				src.setData(data);
			}
			if (map.getLayer(FLOODS_311_LAYER_ID)) {
				map.setLayoutProperty(
					FLOODS_311_LAYER_ID,
					"visibility",
					floods311Toggle.checked ? "visible" : "none",
				);
			}
		} catch {
			floods311Toggle.disabled = true;
		}
	} else {
		floods311Toggle.disabled = true;
		floods311Label.textContent = "311 Reports (Historical AR only)";
		if (map.getLayer(FLOODS_311_LAYER_ID)) {
			map.setLayoutProperty(FLOODS_311_LAYER_ID, "visibility", "none");
		}
	}

	// Traffic Hazards & Water Rescues
	const entryTierC = historical
		? manifestOverlays?.tier_c_by_storm?.[selectedStorm]
		: null;
	if (entryTierC?.url) {
		tierCToggle.disabled = false;
		tierCLabel.textContent = `Traffic & Rescues (${entryTierC.count ?? "?"})`;
		try {
			const data = await fetchGeoJsonCached(entryTierC.url);
			const src = map.getSource(TIER_C_SOURCE_ID);
			if (src) {
				src.setData(data);
			}
			if (map.getLayer(TIER_C_LAYER_ID)) {
				map.setLayoutProperty(
					TIER_C_LAYER_ID,
					"visibility",
					tierCToggle.checked ? "visible" : "none",
				);
			}
		} catch {
			tierCToggle.disabled = true;
		}
	} else {
		tierCToggle.disabled = true;
		tierCLabel.textContent = "Traffic & Rescues (Historical AR only)";
		if (map.getLayer(TIER_C_LAYER_ID)) {
			map.setLayoutProperty(TIER_C_LAYER_ID, "visibility", "none");
		}
	}

	// Tier A: NOAA Tide Gauge pins filtered by storm when historical
	if (tierAGeoJsonCache && map.getSource(TIER_A_SOURCE_ID)) {
		const features = (tierAGeoJsonCache.features ?? []).filter((f) =>
			historical ? f.properties?.storm_id === selectedStorm : true,
		);
		map.getSource(TIER_A_SOURCE_ID).setData({
			type: "FeatureCollection",
			features,
		});
		if (map.getLayer(TIER_A_LAYER_ID)) {
			map.setLayoutProperty(
				TIER_A_LAYER_ID,
				"visibility",
				tierAToggle.checked ? "visible" : "none",
			);
		}
	}
}

function handleSelectionChange() {
	popup.remove();
	const scenario = syncControlsFromState();
	if (scenario) {
		applyScenario(scenario);
	}
	syncStormValidationOverlays();
	syncUrlHash();
}

async function initTopoContoursOverlay(beforeId) {
	const url =
		manifestOverlays?.topo_contours?.url ?? "sf_topo_contours.geojson";
	try {
		const geojson = await fetchGeoJsonCached(url);
		if (!map.getSource(TOPO_CONTOURS_SOURCE_ID)) {
			map.addSource(TOPO_CONTOURS_SOURCE_ID, {
				type: "geojson",
				data: geojson,
			});
		}
		const visibility = terrain3dToggle?.checked ? "visible" : "none";
		if (!map.getLayer(TOPO_CONTOURS_LINE_LAYER_ID)) {
			map.addLayer(
				{
					id: TOPO_CONTOURS_LINE_LAYER_ID,
					type: "line",
					source: TOPO_CONTOURS_SOURCE_ID,
					layout: { visibility },
					paint: {
						"line-color": [
							"case",
							["==", ["get", "is_major"], 1],
							"#475569",
							"#64748b",
						],
						"line-width": ["case", ["==", ["get", "is_major"], 1], 1.4, 0.7],
						"line-opacity": ["case", ["==", ["get", "is_major"], 1], 0.5, 0.28],
					},
				},
				beforeId,
			);
		}
		if (!map.getLayer(TOPO_CONTOURS_LABEL_LAYER_ID)) {
			map.addLayer(
				{
					id: TOPO_CONTOURS_LABEL_LAYER_ID,
					type: "symbol",
					source: TOPO_CONTOURS_SOURCE_ID,
					minzoom: 13,
					filter: ["==", ["get", "is_major"], 1],
					layout: {
						visibility,
						"symbol-placement": "line",
						"text-field": ["concat", ["to-string", ["get", "elev_m"]], " m"],
						"text-size": 10,
					},
					paint: {
						"text-color": "#334155",
						"text-halo-color": "rgba(255, 255, 255, 0.85)",
						"text-halo-width": 1.2,
					},
				},
				beforeId,
			);
		}
	} catch {
		// Optional 3D topo contour overlay; ignore if not exported
	}
}

async function initBenchmarkOverlay(beforeId) {
	const url =
		manifestOverlays?.tier_b_benchmark?.url ??
		"sf_sfpuc_100yr_benchmark.geojson";
	try {
		const geojson = await fetchGeoJsonCached(url);
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

async function initTierENfipOverlay(beforeId) {
	if (!tierEToggle) {
		return;
	}
	const entry = manifestOverlays?.tier_e_nfip;
	const url = entry?.url ?? "sf_tier_e_nfip_tracts.geojson";
	try {
		const geojson = await fetchGeoJsonCached(url);
		const count = entry?.count ?? geojson?.features?.length ?? 0;
		if (tierELabel) {
			tierELabel.textContent = `FEMA NFIP Claims (${count} tracts)`;
		}
		map.addSource(TIER_E_SOURCE_ID, {
			type: "geojson",
			data: geojson,
		});
		const visibility = tierEToggle.checked ? "visible" : "none";
		map.addLayer(
			{
				id: TIER_E_FILL_LAYER_ID,
				type: "fill",
				source: TIER_E_SOURCE_ID,
				layout: { visibility },
				paint: {
					"fill-color": [
						"interpolate",
						["linear"],
						["coalesce", ["get", "claim_count"], 1],
						1,
						"#99f6e4",
						3,
						"#2dd4bf",
						6,
						"#0d9488",
						10,
						"#115e59",
					],
					"fill-opacity": 0.28,
				},
			},
			beforeId,
		);
		map.addLayer(
			{
				id: TIER_E_LINE_LAYER_ID,
				type: "line",
				source: TIER_E_SOURCE_ID,
				layout: { visibility },
				paint: {
					"line-color": "#14b8a6",
					"line-width": 1.4,
					"line-dasharray": [3, 1.5],
				},
			},
			beforeId,
		);
	} catch {
		tierEToggle.disabled = true;
		tierEToggle.title = "NFIP GeoJSON not found in /data/";
	}
}

function initPointLayer(sourceId, layerId, paint, beforeId) {
	map.addSource(sourceId, {
		type: "geojson",
		data: { type: "FeatureCollection", features: [] },
	});
	map.addLayer(
		{
			id: layerId,
			type: "circle",
			source: sourceId,
			layout: { visibility: "none" },
			paint,
		},
		beforeId,
	);
	map.on("mouseenter", layerId, () => {
		map.getCanvas().style.cursor = "pointer";
	});
	map.on("mouseleave", layerId, () => {
		map.getCanvas().style.cursor = "";
	});
}

async function initTierAOverlay(beforeId) {
	const tierAInfo = manifestOverlays?.tier_a_gauges;
	if (!tierAInfo?.url) {
		tierAToggle.disabled = true;
		return;
	}
	try {
		tierAGeoJsonCache = await fetchGeoJsonCached(tierAInfo.url);
		initPointLayer(
			TIER_A_SOURCE_ID,
			TIER_A_LAYER_ID,
			{
				"circle-radius": 7,
				"circle-color": "#10b981",
				"circle-opacity": 0.95,
				"circle-stroke-color": "#ffffff",
				"circle-stroke-width": 2,
			},
			beforeId,
		);
		tierAToggle.disabled = false;
	} catch {
		tierAToggle.disabled = true;
	}
}

async function queryFloodDepthAt(lngLat) {
	if (!activeCogUrl || !activeScenario) {
		return;
	}
	const { lng, lat } = lngLat;
	try {
		const values = await locationValues(
			activeCogUrl,
			{ latitude: lat, longitude: lng },
			Math.round(map.getZoom()),
		);
		const depth = values?.[0];
		const labelHtml = `<strong>${escapeHtml(activeScenario.label)}</strong>`;
		const prefix =
			activeScenario.metric === "delta"
				? "Added SLR depth:"
				: "Max flood depth:";
		const bodyHtml = isDryOrNoData(depth)
			? "Dry / below 2 in (0.05 m)"
			: `${prefix} <strong>${formatDepth(depth)}</strong>`;

		popup.setLngLat(lngLat).setHTML(`${labelHtml}<br/>${bodyHtml}`).addTo(map);
	} catch {
		popup
			.setLngLat(lngLat)
			.setHTML(
				"Unable to query COG (ensure local assets exist in <code>data/web_outputs/</code>).",
			)
			.addTo(map);
	}
}

function initHotspots() {
	if (!hotspotChipsEl) {
		return;
	}
	hotspotChipsEl.replaceChildren();
	for (const spot of HOTSPOTS) {
		const btn = document.createElement("button");
		btn.type = "button";
		btn.className = "hotspot-chip";
		btn.textContent = spot.label;
		btn.addEventListener("click", () => {
			map.flyTo({
				center: [spot.lng, spot.lat],
				zoom: spot.zoom,
				duration: 700,
			});
		});
		hotspotChipsEl.append(btn);
	}
}

function initSearch() {
	if (!searchForm || !searchInput || !searchResultsEl) {
		return;
	}
	searchForm.addEventListener("submit", async (e) => {
		e.preventDefault();
		const q = searchInput.value.trim();
		if (!q) {
			searchResultsEl.hidden = true;
			return;
		}
		setStatus("Searching SF addresses…");
		try {
			const resp = await fetch(buildGeocoderUrl(q));
			if (!resp.ok) {
				throw new Error(`HTTP ${resp.status}`);
			}
			const data = await resp.json();
			const features = data?.features ?? [];
			searchResultsEl.replaceChildren();
			if (features.length === 0) {
				searchResultsEl.hidden = true;
				setStatus("No matching San Francisco address found.", true);
				return;
			}
			for (const feat of features) {
				const [lng, lat] = feat.geometry?.coordinates ?? [];
				if (typeof lng !== "number" || typeof lat !== "number") {
					continue;
				}
				const props = feat.properties ?? {};
				const label = [props.name, props.street, props.city]
					.filter(Boolean)
					.join(", ");
				const li = document.createElement("li");
				const btn = document.createElement("button");
				btn.type = "button";
				btn.textContent = label || `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
				btn.addEventListener("click", () => {
					searchResultsEl.hidden = true;
					map.flyTo({ center: [lng, lat], zoom: 16, duration: 700 });
					map.once("moveend", () => {
						queryFloodDepthAt({ lng, lat });
					});
				});
				li.append(btn);
				searchResultsEl.append(li);
			}
			searchResultsEl.hidden = searchResultsEl.children.length === 0;
			setStatus("Select a search result to inspect flood depth.");
		} catch (err) {
			searchResultsEl.hidden = true;
			setStatus(`Address search failed (${err.message}).`, true);
		}
	});
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

	if (panelSubtitleEl) {
		panelSubtitleEl.textContent = formatManifestSubtitle(manifest);
	}
	manifestOverlays = manifest?.overlays ?? {};
	allScenarios = scenarios;

	const { design, historical } = groupAvailableStorms(allScenarios);
	stormSelect.replaceChildren();

	if (design.length > 0) {
		const designGroup = document.createElement("optgroup");
		designGroup.label =
			"Coincident Stress Scenarios (Atlas 14 Rain + High Tide/SLR)";
		for (const st of design) {
			const option = document.createElement("option");
			option.value = st.id;
			option.textContent = st.label;
			designGroup.append(option);
		}
		stormSelect.append(designGroup);
	}
	if (historical.length > 0) {
		const histGroup = document.createElement("optgroup");
		histGroup.label = "Historical Atmospheric Rivers (Observed Tides)";
		for (const st of historical) {
			const option = document.createElement("option");
			option.value = st.id;
			option.textContent = st.label;
			histGroup.append(option);
		}
		stormSelect.append(histGroup);
	}
	stormSelect.disabled = false;

	const validStormIds = new Set([...design, ...historical].map((st) => st.id));
	if (!selectedStorm || !validStormIds.has(selectedStorm)) {
		selectedStorm = design[0]?.id ?? historical[0]?.id ?? allScenarios[0].storm;
	}
	stormSelect.value = selectedStorm;

	return syncControlsFromState();
}

renderLegend("depth");
initHotspots();
initSearch();

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

	const initialScenario = await manifestPromise;
	ensureContextOverlayLayers(map, beforeId);
	await initTopoContoursOverlay(beforeId);
	await initTierENfipOverlay(beforeId);
	await initBenchmarkOverlay(beforeId);
	initPointLayer(
		FLOODS_311_SOURCE_ID,
		FLOODS_311_LAYER_ID,
		{
			"circle-radius": 4.5,
			"circle-color": "#f43f5e",
			"circle-opacity": 0.9,
			"circle-stroke-color": "#ffffff",
			"circle-stroke-width": 1.2,
		},
		beforeId,
	);
	initPointLayer(
		TIER_C_SOURCE_ID,
		TIER_C_LAYER_ID,
		{
			"circle-radius": 5.5,
			"circle-color": "#a855f7",
			"circle-opacity": 0.92,
			"circle-stroke-color": "#ffffff",
			"circle-stroke-width": 1.4,
		},
		beforeId,
	);
	await initTierAOverlay(beforeId);

	if (initialHash.terrain3d && terrain3dToggle) {
		terrain3dToggle.checked = true;
		applyTerrain3d(true);
	}
	if (elevatedBridgesToggle) {
		applyElevatedBridgesVisibility(elevatedBridgesToggle.checked);
	}

	if (initialScenario) {
		applyScenario(initialScenario);
		await syncStormValidationOverlays();
		syncUrlHash();
	}
});

map.on("moveend", () => {
	syncUrlHash();
});

// Support middle-mouse drag to rotate (horizontal) and tilt/pitch (vertical)
(() => {
	const canvas = map.getCanvas();
	let active = false;
	let startX = 0;
	let startY = 0;
	let startBearing = 0;
	let startPitch = 0;

	canvas.addEventListener("mousedown", (e) => {
		if (e.button !== 1) {
			return;
		}
		e.preventDefault();
		active = true;
		startX = e.clientX;
		startY = e.clientY;
		startBearing = map.getBearing();
		startPitch = map.getPitch();
		canvas.style.cursor = "grabbing";
	});

	window.addEventListener("mousemove", (e) => {
		if (!active) {
			return;
		}
		const dx = e.clientX - startX;
		const dy = e.clientY - startY;
		map.setBearing(startBearing + dx * 0.45);
		if (terrain3dToggle?.checked) {
			map.setPitch(Math.max(0, Math.min(70, startPitch - dy * 0.35)));
		}
	});

	window.addEventListener("mouseup", (e) => {
		if (!active || e.button !== 1) {
			return;
		}
		active = false;
		canvas.style.cursor = "";
		syncUrlHash();
	});
})();

stormSelect.addEventListener("change", () => {
	selectedStorm = stormSelect.value;
	handleSelectionChange();
});

slrSelect.addEventListener("change", () => {
	selectedSlrFt = Number(slrSelect.value);
	handleSelectionChange();
});

metricDepthBtn.addEventListener("click", () => {
	if (selectedMetric !== "depth") {
		selectedMetric = "depth";
		handleSelectionChange();
	}
});

metricDeltaBtn.addEventListener("click", () => {
	if (!metricDeltaBtn.disabled && selectedMetric !== "delta") {
		selectedMetric = "delta";
		handleSelectionChange();
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

elevatedBridgesToggle?.addEventListener("change", () => {
	applyElevatedBridgesVisibility(elevatedBridgesToggle.checked);
});

benchmarkToggle.addEventListener("change", () => {
	const visibility = benchmarkToggle.checked ? "visible" : "none";
	for (const layerId of [BENCHMARK_FILL_LAYER_ID, BENCHMARK_LINE_LAYER_ID]) {
		if (map.getLayer(layerId)) {
			map.setLayoutProperty(layerId, "visibility", visibility);
		}
	}
});

floods311Toggle.addEventListener("change", () => {
	const visibility = floods311Toggle.checked ? "visible" : "none";
	if (map.getLayer(FLOODS_311_LAYER_ID)) {
		map.setLayoutProperty(FLOODS_311_LAYER_ID, "visibility", visibility);
	}
});

tierCToggle?.addEventListener("change", () => {
	const visibility = tierCToggle.checked ? "visible" : "none";
	if (map.getLayer(TIER_C_LAYER_ID)) {
		map.setLayoutProperty(TIER_C_LAYER_ID, "visibility", visibility);
	}
});

tierAToggle?.addEventListener("change", () => {
	const visibility = tierAToggle.checked ? "visible" : "none";
	if (map.getLayer(TIER_A_LAYER_ID)) {
		map.setLayoutProperty(TIER_A_LAYER_ID, "visibility", visibility);
	}
});

tierEToggle?.addEventListener("change", () => {
	const visible = tierEToggle.checked;
	if (nfipLegendEl) {
		nfipLegendEl.hidden = !visible;
	}
	const visibility = visible ? "visible" : "none";
	for (const layerId of [TIER_E_FILL_LAYER_ID, TIER_E_LINE_LAYER_ID]) {
		if (map.getLayer(layerId)) {
			map.setLayoutProperty(layerId, "visibility", visibility);
		}
	}
});

map.on("click", async (e) => {
	const hitBbox = [
		[e.point.x - 6, e.point.y - 6],
		[e.point.x + 6, e.point.y + 6],
	];

	if (map.getLayer(TIER_A_LAYER_ID)) {
		const hitA = map.queryRenderedFeatures(hitBbox, {
			layers: [TIER_A_LAYER_ID],
		});
		if (hitA.length > 0) {
			const p = hitA[0].properties ?? {};
			const obs = Number(p.observed_peak_navd88_m);
			const mod = Number(p.modeled_peak_navd88_m);
			const bias = Number(p.bias_m);
			popup
				.setLngLat(e.lngLat)
				.setHTML(
					`<strong>NOAA Tide Gauge: ${escapeHtml(p.name || p.station_id)} (${escapeHtml(p.station_id)})</strong><br/>` +
						`Storm: ${escapeHtml(p.storm_id || "")}<br/>` +
						`Observed Peak: <strong>${Number.isFinite(obs) ? obs.toFixed(3) : "?"} m</strong> NAVD88<br/>` +
						`Modeled Peak: <strong>${Number.isFinite(mod) ? mod.toFixed(3) : "?"} m</strong> NAVD88<br/>` +
						`Bias: ${Number.isFinite(bias) ? bias.toFixed(4) : "?"} m · Timing err: ${escapeHtml(p.peak_timing_error_min ?? 0)} min`,
				)
				.addTo(map);
			return;
		}
	}

	if (map.getLayer(TIER_C_LAYER_ID)) {
		const hitC = map.queryRenderedFeatures(hitBbox, {
			layers: [TIER_C_LAYER_ID],
		});
		if (hitC.length > 0) {
			const p = hitC[0].properties ?? {};
			popup
				.setLngLat(e.lngLat)
				.setHTML(
					`<strong>${escapeHtml(p.category || "Roadway Disruption")}</strong><br/>` +
						`Source: ${escapeHtml(p.source || "datasf")} · ${escapeHtml(p.timestamp || "")}`,
				)
				.addTo(map);
			return;
		}
	}

	if (map.getLayer(FLOODS_311_LAYER_ID)) {
		const hit311 = map.queryRenderedFeatures(hitBbox, {
			layers: [FLOODS_311_LAYER_ID],
		});
		if (hit311.length > 0) {
			const props = hit311[0].properties ?? {};
			const title = escapeHtml(props.service_subtype || "311 Flood Report");
			const details = escapeHtml(props.service_details || "");
			const addr = escapeHtml(props.address || "Address unavailable");
			const nbhd = escapeHtml(props.neighborhood || "");
			popup
				.setLngLat(e.lngLat)
				.setHTML(
					`<strong>311 Report: ${title}</strong>` +
						`${details ? ` (${details})` : ""}<br/>` +
						`${addr}${nbhd ? ` — ${nbhd}` : ""}`,
				)
				.addTo(map);
			return;
		}
	}

	if (map.getLayer(TIER_E_FILL_LAYER_ID)) {
		const hitE = map.queryRenderedFeatures(e.point, {
			layers: [TIER_E_FILL_LAYER_ID],
		});
		if (hitE.length > 0) {
			const p = hitE[0].properties ?? {};
			const paidUsd = Number(p.total_paid_usd ?? 0);
			const formattedPaid = Number.isFinite(paidUsd)
				? paidUsd.toLocaleString("en-US", {
						style: "currency",
						currency: "USD",
						maximumFractionDigits: 0,
					})
				: "$0";
			popup
				.setLngLat(e.lngLat)
				.setHTML(
					`<strong>FEMA NFIP Tract ${escapeHtml(p.census_tract || "")}</strong><br/>` +
						`Claims: ${escapeHtml(p.claim_count ?? 0)} (${escapeHtml(p.paid_claim_count ?? 0)} paid)<br/>` +
						`Total Paid: ${escapeHtml(formattedPaid)}`,
				)
				.addTo(map);
			return;
		}
	}

	await queryFloodDepthAt(e.lngLat);
});

const DISCLAIMER_STORAGE_KEY = "bayUrbanFloodMapDisclaimerAcceptedV1";

function initDisclaimerModal() {
	const modal = document.getElementById("disclaimer-modal");
	const agreeBtn = document.getElementById("disclaimer-agree-btn");
	const reopenBtn = document.getElementById("reopen-disclaimer-btn");
	if (!modal || !agreeBtn) {
		return;
	}

	let accepted = false;
	try {
		accepted = window.localStorage.getItem(DISCLAIMER_STORAGE_KEY) === "1";
	} catch {
		accepted = false;
	}

	if (!accepted) {
		modal.hidden = false;
		agreeBtn.focus();
	}

	agreeBtn.addEventListener("click", () => {
		try {
			window.localStorage.setItem(DISCLAIMER_STORAGE_KEY, "1");
		} catch {
			// Ignore storage errors (e.g. private browsing) and still dismiss.
		}
		modal.hidden = true;
		reopenBtn?.focus();
	});

	reopenBtn?.addEventListener("click", () => {
		modal.hidden = false;
		agreeBtn.focus();
	});
}

initDisclaimerModal();

function initPanelToggle() {
	const controls = document.getElementById("controls");
	const panelBody = document.getElementById("panel-body");
	const toggleBtn = document.getElementById("panel-toggle-btn");
	if (!controls || !panelBody || !toggleBtn) {
		return;
	}

	function setCollapsed(collapsed) {
		panelBody.hidden = collapsed;
		controls.classList.toggle("collapsed", collapsed);
		toggleBtn.setAttribute("aria-expanded", String(!collapsed));
		toggleBtn.setAttribute(
			"aria-label",
			collapsed ? "Show controls" : "Hide controls",
		);
		toggleBtn.textContent = collapsed ? "Show" : "Hide";
		requestAnimationFrame(() => map.resize());
	}

	setCollapsed(window.matchMedia("(max-width: 640px)").matches);

	toggleBtn.addEventListener("click", () => {
		setCollapsed(!panelBody.hidden);
	});
}

initPanelToggle();
