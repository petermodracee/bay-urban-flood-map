import assert from "node:assert/strict";
import test from "node:test";
import { normalizeDataBaseUrl, resolveDataAssetUrl } from "./dataUrls.js";

test("normalizeDataBaseUrl falls back to /data and trims trailing slashes", () => {
	assert.equal(normalizeDataBaseUrl(undefined), "/data");
	assert.equal(normalizeDataBaseUrl(""), "/data");
	assert.equal(normalizeDataBaseUrl("   "), "/data");
	assert.equal(
		normalizeDataBaseUrl("https://pub-123.r2.dev/"),
		"https://pub-123.r2.dev",
	);
});

test("resolveDataAssetUrl resolves local /data assets by default", () => {
	assert.equal(
		resolveDataAssetUrl("manifest.json", undefined, "http://localhost:5173"),
		"http://localhost:5173/data/manifest.json",
	);
	assert.equal(
		resolveDataAssetUrl(
			"/data/sf_citywide_100yr_slr0ft_depth_3857.tif",
			"",
			"http://localhost:5173/",
		),
		"http://localhost:5173/data/sf_citywide_100yr_slr0ft_depth_3857.tif",
	);
});

test("resolveDataAssetUrl resolves remote VITE_DATA_BASE_URL without duplicating /data/", () => {
	assert.equal(
		resolveDataAssetUrl(
			"manifest.json",
			"https://pub-abc.r2.dev/",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/manifest.json",
	);
	assert.equal(
		resolveDataAssetUrl(
			"/data/sf_citywide_100yr_slr0ft_depth_3857.tif",
			"https://pub-abc.r2.dev",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/sf_citywide_100yr_slr0ft_depth_3857.tif",
	);
	assert.equal(
		resolveDataAssetUrl(
			"sf_sfpuc_100yr_benchmark.geojson",
			"https://pub-abc.r2.dev/v02/",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/v02/sf_sfpuc_100yr_benchmark.geojson",
	);
});

test("resolveDataAssetUrl preserves already-absolute URLs", () => {
	assert.equal(
		resolveDataAssetUrl(
			"https://cdn.example.com/custom.tif",
			"https://pub-abc.r2.dev",
			"http://localhost:5173",
		),
		"https://cdn.example.com/custom.tif",
	);
});
