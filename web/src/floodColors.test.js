import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
	createFloodColorRamp,
	DELTA_LEGEND_BINS,
	DEPTH_LEGEND_BINS,
	formatDepth,
	getLegendBins,
	isDryOrNoData,
} from "./floodColors.js";

describe("floodColors", () => {
	it("formats depth in inches below 12 in and feet at or above 12 in", () => {
		assert.equal(formatDepth(0.2), "7.9 in (0.20 m)");
		assert.equal(formatDepth(0.36), "1.18 ft (0.36 m)");
		assert.equal(formatDepth(1.0), "3.28 ft (1.00 m)");
	});

	it("identifies dry, trace, NaN, and NoData values", () => {
		assert.equal(isDryOrNoData(undefined), true);
		assert.equal(isDryOrNoData(Number.NaN), true);
		assert.equal(isDryOrNoData(-9999), true);
		assert.equal(isDryOrNoData(0.049), true);
		assert.equal(isDryOrNoData(0.05), false);
		assert.equal(isDryOrNoData(0.5), false);
	});

	it("maps depth and delta stops and masks dry/NoData pixels transparently", () => {
		const depthRamp = createFloodColorRamp("depth");
		const out = new Uint8ClampedArray(4);

		depthRamp(new Float32Array([-9999]), out, { noData: -9999 });
		assert.deepEqual(Array.from(out), [0, 0, 0, 0]);

		depthRamp(new Float32Array([0.02]), out, { noData: -9999 });
		assert.deepEqual(Array.from(out), [0, 0, 0, 0]);

		depthRamp(new Float32Array([0.05]), out, { noData: -9999 });
		assert.deepEqual(Array.from(out), [186, 230, 253, 150]);

		depthRamp(new Float32Array([3.0]), out, { noData: -9999 });
		assert.deepEqual(Array.from(out), [88, 28, 135, 245]);

		const deltaRamp = createFloodColorRamp("delta");
		deltaRamp(new Float32Array([0.05]), out, { noData: -9999 });
		assert.deepEqual(Array.from(out), [253, 230, 138, 155]);

		deltaRamp(new Float32Array([2.5]), out, { noData: -9999 });
		assert.deepEqual(Array.from(out), [127, 29, 29, 250]);
	});

	it("returns 4 legend bins for depth and delta metrics", () => {
		assert.equal(getLegendBins("depth"), DEPTH_LEGEND_BINS);
		assert.equal(getLegendBins("delta"), DELTA_LEGEND_BINS);
		assert.equal(DEPTH_LEGEND_BINS.length, 4);
		assert.equal(DELTA_LEGEND_BINS.length, 4);
	});
});
