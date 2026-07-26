export function describeVoiceError(error: unknown): string {
  const raw = extractErrorText(error);
  if (!raw) {
    return "语音暂时不可用。";
  }

  if (looksLikeMicrophoneError(raw)) {
    return "无法访问麦克风，请检查系统权限和输入设备。";
  }
  if (raw.includes("没有录到声音")) {
    return "没有录到声音，请再说一次。";
  }
  if (raw.includes("没有识别到有效语音")) {
    return "没有识别到有效语音，请再说清楚一点。";
  }
  if (looksLikeBridgeConnectionError(raw)) {
    return `语音 bridge 连接失败：${raw}`;
  }
  return raw;
}

function extractErrorText(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message.trim();
  }
  if (typeof error === "string") {
    return error.trim();
  }
  return "";
}

function looksLikeBridgeConnectionError(message: string): boolean {
  return (
    message.includes("Desktop voice bridge") ||
    message.includes("voice bridge") ||
    message.includes("WebSocket") ||
    message.includes("desktop voice request") ||
    message.includes("desktop voice")
  );
}

function looksLikeMicrophoneError(message: string): boolean {
  const normalized = message.toLowerCase();
  return (
    normalized.includes("microphone") ||
    normalized.includes("麦克风") ||
    normalized.includes("permission denied") ||
    normalized.includes("notallowederror") ||
    normalized.includes("notfounderror") ||
    normalized.includes("device not found")
  );
}
