/**
 * Normalize VITE_DATA_BASE_URL, defaulting to "/data" when empty or unset.
 */
export function normalizeDataBaseUrl(rawBaseUrl) {
	const trimmed = typeof rawBaseUrl === "string" ? rawBaseUrl.trim() : "";
	if (!trimmed) {
		return "/data";
	}
	return trimmed.replace(/\/+$/, "") || "/data";
}

/**
 * Normalize an optional build-time VITE_DATA_VERSION (e.g. "v0.6.1" or "0.6.1").
 * Returns "" when unset or empty.
 */
export function normalizeDataVersion(rawVersion) {
	const trimmed =
		typeof rawVersion === "string"
			? rawVersion.trim().replace(/^\/+|\/+$/g, "")
			: "";
	if (!trimmed) {
		return "";
	}
	return trimmed.startsWith("v") ? trimmed : `v${trimmed}`;
}

/**
 * Resolve a manifest/asset path or URL against VITE_DATA_BASE_URL and origin.
 * Preserves absolute URLs, strips leading "/data/" when targeting a custom base
 * URL (such as an R2 public bucket), and prepends origin for relative paths.
 */
export function resolveDataAssetUrl(pathOrUrl, rawBaseUrl, origin = "") {
	const target = String(pathOrUrl ?? "").trim();
	if (/^https?:\/\//i.test(target)) {
		return target;
	}

	const base = normalizeDataBaseUrl(rawBaseUrl);
	let relativePath = target.replace(/^\/+/, "");
	if (base !== "/data" && relativePath.startsWith("data/")) {
		relativePath = relativePath.slice("data/".length);
	} else if (base === "/data" && relativePath.startsWith("data/")) {
		relativePath = relativePath.slice("data/".length);
	}

	const joined = relativePath ? `${base}/${relativePath}` : base;
	if (/^https?:\/\//i.test(joined)) {
		return joined;
	}
	const cleanOrigin = String(origin ?? "").replace(/\/+$/, "");
	const cleanPath = joined.startsWith("/") ? joined : `/${joined}`;
	return `${cleanOrigin}${cleanPath}`;
}

/**
 * Resolve the unversioned latest.json pointer URL against VITE_DATA_BASE_URL.
 */
export function resolveLatestPointerUrl(rawBaseUrl, origin = "") {
	return resolveDataAssetUrl("latest.json", rawBaseUrl, origin);
}

/**
 * Resolve a manifest URL for an explicit version prefix (e.g. "v0.6.1/manifest.json"),
 * or the unversioned "manifest.json" when rawVersion is empty.
 */
export function resolveVersionedManifestUrl(
	rawVersion,
	rawBaseUrl,
	origin = "",
) {
	const version = normalizeDataVersion(rawVersion);
	const manifestPath = version ? `${version}/manifest.json` : "manifest.json";
	return resolveDataAssetUrl(manifestPath, rawBaseUrl, origin);
}

/**
 * Resolve the manifest URL declared inside a latest.json pointer payload:
 * {"version": "v0.6.1", "manifest": "v0.6.1/manifest.json"}.
 */
export function resolveManifestFromLatestPointer(
	latestPayload,
	rawBaseUrl,
	origin = "",
) {
	const manifestPath =
		typeof latestPayload?.manifest === "string"
			? latestPayload.manifest.trim()
			: "";
	if (manifestPath) {
		return resolveDataAssetUrl(manifestPath, rawBaseUrl, origin);
	}
	const version = normalizeDataVersion(latestPayload?.version);
	if (version) {
		return resolveVersionedManifestUrl(version, rawBaseUrl, origin);
	}
	throw new Error("latest.json is missing 'manifest' and 'version' fields");
}

/**
 * Resolve the active manifest.json URL:
 * 1. If VITE_DATA_VERSION is set, load "<version>/manifest.json" directly.
 * 2. Otherwise, fetch "latest.json" and follow its "manifest" pointer.
 * 3. If "latest.json" returns 404 (e.g., local dev or legacy unversioned bucket),
 *    fall back to unversioned "manifest.json".
 */
export async function resolveManifestUrl({
	rawBaseUrl,
	rawDataVersion,
	origin = "",
	fetchImpl = fetch,
}) {
	const pinnedVersion = normalizeDataVersion(rawDataVersion);
	if (pinnedVersion) {
		return resolveVersionedManifestUrl(pinnedVersion, rawBaseUrl, origin);
	}

	const pointerUrl = resolveLatestPointerUrl(rawBaseUrl, origin);
	const response = await fetchImpl(pointerUrl, { cache: "no-store" });
	if (response.ok) {
		const pointer = await response.json();
		return resolveManifestFromLatestPointer(pointer, rawBaseUrl, origin);
	}
	if (response.status === 404) {
		return resolveDataAssetUrl("manifest.json", rawBaseUrl, origin);
	}
	throw new Error(`HTTP ${response.status} loading latest.json`);
}
