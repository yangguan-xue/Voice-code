import { describe, expect, it } from "vitest";
import { concatenateFloat32, encodePcm16Wav, resampleLinear } from "../voice-recorder";

describe("voice-recorder audio helpers", () => {
  it("concatenates float32 chunks in order", () => {
    const result = concatenateFloat32([new Float32Array([1, 2]), new Float32Array([3])]);

    expect(Array.from(result)).toEqual([1, 2, 3]);
  });

  it("resamples audio with linear interpolation", () => {
    const result = resampleLinear(new Float32Array([0, 1, 0, -1]), 4, 2);

    expect(Array.from(result)).toEqual([0, 0]);
  });

  it("encodes mono 16-bit wav bytes", () => {
    const wav = encodePcm16Wav(new Float32Array([0, 1, -1]), 16000);

    expect(String.fromCharCode(...wav.slice(0, 4))).toBe("RIFF");
    expect(String.fromCharCode(...wav.slice(8, 12))).toBe("WAVE");
    expect(wav).toHaveLength(50);
  });
});
