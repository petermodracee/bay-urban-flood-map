import assert from "node:assert/strict";
import test from "node:test";
import {
	normalizeDataBaseUrl,
	normalizeDataVersion,
	resolveDataAssetUrl,
	resolveLatestPointerUrl,
	resolveManifestFromLatestPointer,
	resolveManifestUrl,
	resolveVersionedManifestUrl,
} from "./dataUrls.js";

test("normalizeDataBaseUrl falls back to /data and trims trailing slashes", () => {
	assert.equal(normalizeDataBaseUrl(undefined), "/data");
	assert.equal(normalizeDataBaseUrl(""), "/data");
	assert.equal(normalizeDataBaseUrl("   "), "/data");
	assert.equal(
		normalizeDataBaseUrl("https://pub-123.r2.dev/"),
		"https://pub-123.r2.dev",
	);
});

test("normalizeDataVersion trims slashes and ensures a leading v", () => {
	assert.equal(normalizeDataVersion(undefined), "");
	assert.equal(normalizeDataVersion(""), "");
	assert.equal(normalizeDataVersion("   "), "");
	assert.equal(normalizeDataVersion("v0.6.1"), "v0.6.1");
	assert.equal(normalizeDataVersion("0.6.1"), "v0.6.1");
	assert.equal(normalizeDataVersion("/v0.6.1/"), "v0.6.1");
});

test("resolveDataAssetUrl resolves local /data assets by default", () => {
	assert.equal(
		resolveDataAssetUrl("manifest.json", undefined, "http://localhost:5173"),
		"http://localhost:5173/data/manifest.json",
	);
	assert.equal(
		resolveDataAssetUrl(
			"/data/v0.6.1/sf_citywide_100yr_slr0ft_depth_3857.tif",
			"",
			"http://localhost:5173/",
		),
		"http://localhost:5173/data/v0.6.1/sf_citywide_100yr_slr0ft_depth_3857.tif",
	);
	assert.equal(
		resolveDataAssetUrl(
			"v0.6.1/sf_citywide_100yr_slr0ft_depth_3857.tif",
			undefined,
			"http://localhost:5173",
		),
		"http://localhost:5173/data/v0.6.1/sf_citywide_100yr_slr0ft_depth_3857.tif",
	);
});

test("resolveDataAssetUrl resolves remote VITE_DATA_BASE_URL without duplicating /data/", () => {
	assert.equal(
		resolveDataAssetUrl(
			"latest.json",
			"https://pub-abc.r2.dev/",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/latest.json",
	);
	assert.equal(
		resolveDataAssetUrl(
			"/data/v0.6.1/sf_citywide_100yr_slr0ft_depth_3857.tif",
			"https://pub-abc.r2.dev",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/v0.6.1/sf_citywide_100yr_slr0ft_depth_3857.tif",
	);
	assert.equal(
		resolveDataAssetUrl(
			"v0.6.1/sf_sfpuc_100yr_benchmark.geojson",
			"https://pub-abc.r2.dev/",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/v0.6.1/sf_sfpuc_100yr_benchmark.geojson",
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

test("resolveLatestPointerUrl and resolveVersionedManifestUrl build versioned paths", () => {
	assert.equal(
		resolveLatestPointerUrl(
			"https://pub-abc.r2.dev",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/latest.json",
	);
	assert.equal(
		resolveLatestPointerUrl(undefined, "http://localhost:5173"),
		"http://localhost:5173/data/latest.json",
	);
	assert.equal(
		resolveVersionedManifestUrl(
			"v0.6.1",
			"https://pub-abc.r2.dev",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/v0.6.1/manifest.json",
	);
	assert.equal(
		resolveVersionedManifestUrl("0.6.1", undefined, "http://localhost:5173"),
		"http://localhost:5173/data/v0.6.1/manifest.json",
	);
	assert.equal(
		resolveVersionedManifestUrl("", "https://pub-abc.r2.dev"),
		"https://pub-abc.r2.dev/manifest.json",
	);
});

test("resolveManifestFromLatestPointer uses manifest field or falls back to version", () => {
	assert.equal(
		resolveManifestFromLatestPointer(
			{ version: "v0.6.1", manifest: "v0.6.1/manifest.json" },
			"https://pub-abc.r2.dev",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/v0.6.1/manifest.json",
	);
	assert.equal(
		resolveManifestFromLatestPointer(
			{ version: "v0.6.1" },
			"https://pub-abc.r2.dev",
			"https://flood.example.com",
		),
		"https://pub-abc.r2.dev/v0.6.1/manifest.json",
	);
	assert.throws(
		() =>
			resolveManifestFromLatestPointer(
				{},
				"https://pub-abc.r2.dev",
				"https://flood.example.com",
			),
		/missing 'manifest' and 'version'/i,
	);
});

test("resolveManifestUrl pins to VITE_DATA_VERSION without fetching latest.json", async () => {
	let fetchCalled = false;
	const url = await resolveManifestUrl({
		rawBaseUrl: "https://pub-abc.r2.dev",
		rawDataVersion: "v0.6.1",
		origin: "https://flood.example.com",
		fetchImpl: async () => {
			fetchCalled = true;
			throw new Error("should not be called");
		},
	});
	assert.equal(fetchCalled, false);
	assert.equal(url, "https://pub-abc.r2.dev/v0.6.1/manifest.json");
});

test("resolveManifestUrl resolves via latest.json and falls back on 404", async () => {
	const requested = [];
	const resolvedFromLatest = await resolveManifestUrl({
		rawBaseUrl: "https://pub-abc.r2.dev",
		rawDataVersion: undefined,
		origin: "https://flood.example.com",
		fetchImpl: async (url) => {
			requested.push(url);
			return {
				ok: true,
				status: 200,
				json: async () => ({
					version: "v0.6.1",
					manifest: "v0.6.1/manifest.json",
				}),
			};
		},
	});
	assert.deepEqual(requested, ["https://pub-abc.r2.dev/latest.json"]);
	assert.equal(
		resolvedFromLatest,
		"https://pub-abc.r2.dev/v0.6.1/manifest.json",
	);

	const fallbackOn404 = await resolveManifestUrl({
		rawBaseUrl: undefined,
		rawDataVersion: "",
		origin: "http://localhost:5173",
		fetchImpl: async () => ({ ok: false, status: 404 }),
	});
	assert.equal(fallbackOn404, "http://localhost:5173/data/manifest.json");

	await assert.rejects(
		() =>
			resolveManifestUrl({
				rawBaseUrl: "https://pub-abc.r2.dev",
				rawDataVersion: "",
				origin: "https://flood.example.com",
				fetchImpl: async () => ({ ok: false, status: 503 }),
			}),
		/HTTP 503 loading latest\.json/,
	);
});
