import { readString, type BridgeEvent } from "../../lib/bridge/protocol";
import type { VoiceUiEvent, VoiceUiState } from "./voice-types";

type RawVoiceEvent = {
  type?: unknown;
  event?: unknown;
  payload?: unknown;
  text?: unknown;
  state?: unknown;
  message?: unknown;
  recoverable?: unknown;
};

const DROPPED_NAME_PATTERNS = [
  "tool",
  "route",
  "routing",
  "classifier",
  "classification",
  "debug",
  "bridge",
  "websocket"
];

export function toVoiceUiEvent(event: unknown): VoiceUiEvent | null {
  if (isBridgeEventLike(event)) {
    return bridgeEventToVoiceUiEvent(event);
  }

  if (!event || typeof event !== "object") {
    return null;
  }

  const raw = event as RawVoiceEvent;
  const type = typeof raw.type === "string" ? raw.type : "";
  if (!type || shouldDropEventName(type)) {
    return null;
  }

  switch (type) {
    case "voice.state":
      return readVoiceStateEvent(raw);
    case "voice.user.partial":
      return readTextEvent(raw, "voice.user.partial");
    case "voice.user.final":
      return readTextEvent(raw, "voice.user.final");
    case "voice.assistant.delta":
      return readTextEvent(raw, "voice.assistant.delta");
    case "voice.assistant.final":
      return readTextEvent(raw, "voice.assistant.final");
    case "voice.error":
      return readErrorEvent(raw);
    default:
      return null;
  }
}

function bridgeEventToVoiceUiEvent(event: BridgeEvent): VoiceUiEvent | null {
  const eventName = event.event as string;
  if (eventName.startsWith("voice.")) {
    return voiceEnvelopeToVoiceUiEvent(eventName, event.payload, event);
  }

  if (shouldDropEventName(eventName)) {
    return null;
  }

  switch (event.event) {
    case "agent.turn.started": {
      const text = readString(event.payload, "userText");
      return text ? { type: "voice.user.final", text } : null;
    }
    case "agent.text.delta": {
      const text = readString(event.payload, "content");
      return text ? { type: "voice.assistant.delta", text } : null;
    }
    case "agent.turn.finish":
      return { type: "voice.state", state: "idle", text: "准备好了" };
    case "agent.error": {
      if (readString(event.payload, "toolCallId")) {
        return null;
      }
      const message = humanizeError(readString(event.payload, "content"));
      return message ? { type: "voice.error", message, recoverable: true } : null;
    }
    case "permission.request":
      return {
        type: "voice.error",
        message: "需要在主会话中确认权限后继续。",
        recoverable: true
      };
    case "agent.reasoning.delta":
    case "session.updated":
    default:
      return null;
  }
}

function voiceEnvelopeToVoiceUiEvent(
  eventName: string,
  payload: Record<string, unknown>,
  envelope?: BridgeEvent
): VoiceUiEvent | null {
  const metadata = voiceMetadata(envelope);
  switch (eventName) {
    case "voice.state": {
      const state = payload.state;
      if (!isVoiceUiState(state)) {
        return null;
      }
      const text = readString(payload, "text") || undefined;
      return { type: "voice.state", state, text, ...metadata };
    }
    case "voice.user.partial": {
      const text = readString(payload, "text");
      return text ? { type: "voice.user.partial", text, ...metadata } : null;
    }
    case "voice.user.final": {
      const text = readString(payload, "text");
      return text ? { type: "voice.user.final", text, ...metadata } : null;
    }
    case "voice.assistant.delta": {
      const text = readString(payload, "text");
      return text ? { type: "voice.assistant.delta", text, ...metadata } : null;
    }
    case "voice.assistant.final": {
      const text = readString(payload, "text");
      return text ? { type: "voice.assistant.final", text, ...metadata } : null;
    }
    case "voice.error": {
      const message = readString(payload, "message");
      return message
        ? {
            type: "voice.error",
            message: humanizeError(message),
            recoverable: payload.recoverable !== false,
            ...metadata
          }
        : null;
    }
    default:
      return null;
  }
}

function shouldDropEventName(name: string): boolean {
  const normalized = name.toLowerCase();
  return DROPPED_NAME_PATTERNS.some((pattern) => normalized.includes(pattern));
}

function voiceMetadata(envelope?: BridgeEvent): { sessionId?: string; turnId?: number } {
  return {
    ...(envelope?.sessionId ? { sessionId: envelope.sessionId } : {}),
    ...(typeof envelope?.turnId === "number" ? { turnId: envelope.turnId } : {})
  };
}

function readVoiceStateEvent(event: RawVoiceEvent): VoiceUiEvent | null {
  if (!isVoiceUiState(event.state)) {
    return null;
  }

  const text = typeof event.text === "string" ? event.text : undefined;
  return { type: "voice.state", state: event.state, text };
}

function readTextEvent<T extends VoiceUiEvent["type"]>(
  event: RawVoiceEvent,
  type: T
): Extract<VoiceUiEvent, { type: T }> | null {
  if (typeof event.text !== "string" || !event.text.trim()) {
    return null;
  }

  return { type, text: event.text } as Extract<VoiceUiEvent, { type: T }>;
}

function readErrorEvent(event: RawVoiceEvent): VoiceUiEvent | null {
  if (typeof event.message !== "string" || !event.message.trim()) {
    return null;
  }

  return {
    type: "voice.error",
    message: humanizeError(event.message),
    recoverable: event.recoverable !== false
  };
}

function isVoiceUiState(value: unknown): value is VoiceUiState {
  return (
    value === "idle" ||
    value === "listening" ||
    value === "transcribing" ||
    value === "thinking" ||
    value === "speaking" ||
    value === "paused" ||
    value === "error"
  );
}

function isBridgeEventLike(value: unknown): value is BridgeEvent {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Partial<BridgeEvent>;
  return candidate.type === "event" && typeof candidate.event === "string";
}

function humanizeError(message: string): string {
  return message
    .replace(/<[^>]+>/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
