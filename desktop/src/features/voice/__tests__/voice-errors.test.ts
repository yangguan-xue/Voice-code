import { describe, expect, it } from "vitest";
import { describeVoiceError } from "../voice-errors";

describe("describeVoiceError", () => {
  it("keeps bridge failures labeled as bridge failures", () => {
    expect(describeVoiceError(new Error("Desktop voice bridge connection failed."))).toBe(
      "语音 bridge 连接失败：Desktop voice bridge connection failed."
    );
  });

  it("maps microphone failures to a device hint", () => {
    expect(describeVoiceError(new Error("NotAllowedError: microphone permission denied"))).toBe(
      "无法访问麦克风，请检查系统权限和输入设备。"
    );
  });

  it("maps empty recordings to a retry hint", () => {
    expect(describeVoiceError(new Error("没有录到声音。"))).toBe(
      "没有录到声音，请再说一次。"
    );
  });
});
