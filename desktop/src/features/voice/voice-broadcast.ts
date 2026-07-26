import type { VoiceUiEvent } from "./voice-types";

const VOICE_CHANNEL = "voice-ui-events";
const VOICE_PERMISSION_REQUEST_CHANNEL = "voice-permission-requests";
const VOICE_PERMISSION_RESOLUTION_CHANNEL = "voice-permission-resolutions";

export type VoicePermissionRequest = {
  requestId: string;
  toolName: string;
  reason: string;
};

export type VoicePermissionResolution = {
  requestId: string;
  behavior: "allow" | "deny";
  rememberScope: "" | "session" | "workspace";
};

export function broadcastVoiceUiEvent(event: VoiceUiEvent): void {
  broadcast(VOICE_CHANNEL, event);
}

export function subscribeToVoiceUiEvents(
  listener: (event: VoiceUiEvent) => void
): () => void {
  return subscribe(VOICE_CHANNEL, listener);
}

export function broadcastVoicePermissionRequest(request: VoicePermissionRequest): void {
  broadcastRaw(VOICE_PERMISSION_REQUEST_CHANNEL, request);
}

export function subscribeToVoicePermissionRequests(
  listener: (request: VoicePermissionRequest) => void
): () => void {
  return subscribeRaw(VOICE_PERMISSION_REQUEST_CHANNEL, isVoicePermissionRequest, listener);
}

export function broadcastVoicePermissionResolution(resolution: VoicePermissionResolution): void {
  broadcastRaw(VOICE_PERMISSION_RESOLUTION_CHANNEL, resolution);
}

export function subscribeToVoicePermissionResolutions(
  listener: (resolution: VoicePermissionResolution) => void
): () => void {
  return subscribeRaw(
    VOICE_PERMISSION_RESOLUTION_CHANNEL,
    isVoicePermissionResolution,
    listener
  );
}

function broadcast(channelName: string, event: VoiceUiEvent): void {
  broadcastRaw(channelName, event);
}

function broadcastRaw(channelName: string, payload: unknown): void {
  if (typeof BroadcastChannel === "undefined") {
    return;
  }

  const channel = new BroadcastChannel(channelName);
  channel.postMessage(payload);
  channel.close();
}

function subscribe(
  channelName: string,
  listener: (event: VoiceUiEvent) => void
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => undefined;
  }

  const channel = new BroadcastChannel(channelName);
  channel.onmessage = (message) => {
    if (isVoiceUiEvent(message.data)) {
      listener(message.data);
    }
  };

  return () => channel.close();
}

function subscribeRaw<T>(
  channelName: string,
  guard: (value: unknown) => value is T,
  listener: (value: T) => void
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => undefined;
  }

  const channel = new BroadcastChannel(channelName);
  channel.onmessage = (message) => {
    if (guard(message.data)) {
      listener(message.data);
    }
  };

  return () => channel.close();
}

function isVoiceUiEvent(value: unknown): value is VoiceUiEvent {
  if (!value || typeof value !== "object") {
    return false;
  }

  const event = value as Partial<VoiceUiEvent>;
  return typeof event.type === "string" && event.type.startsWith("voice.");
}

function isVoicePermissionRequest(value: unknown): value is VoicePermissionRequest {
  if (!value || typeof value !== "object") {
    return false;
  }
  const request = value as Partial<VoicePermissionRequest>;
  return (
    typeof request.requestId === "string" &&
    typeof request.toolName === "string" &&
    typeof request.reason === "string"
  );
}

function isVoicePermissionResolution(value: unknown): value is VoicePermissionResolution {
  if (!value || typeof value !== "object") {
    return false;
  }
  const resolution = value as Partial<VoicePermissionResolution>;
  return (
    typeof resolution.requestId === "string" &&
    (resolution.behavior === "allow" || resolution.behavior === "deny") &&
    (resolution.rememberScope === "" ||
      resolution.rememberScope === "session" ||
      resolution.rememberScope === "workspace")
  );
}
