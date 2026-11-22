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
