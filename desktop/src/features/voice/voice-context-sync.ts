import type { VoiceLaunchContext } from "./voice-launch-context";

const VOICE_CONTEXT_CHANNEL = "voice-context-snapshots";
const VOICE_EXECUTION_CHANNEL = "voice-execution-state";
const VOICE_CONTROL_CHANNEL = "voice-control-requests";

export type VoiceContextSnapshot = VoiceLaunchContext;

export function broadcastVoiceContextSnapshot(snapshot: VoiceContextSnapshot): void {
  if (typeof BroadcastChannel === "undefined") {
    return;
  }
  const channel = new BroadcastChannel(VOICE_CONTEXT_CHANNEL);
  channel.postMessage(snapshot);
  channel.close();
}

export function subscribeToVoiceContextSnapshots(
  listener: (snapshot: VoiceContextSnapshot) => void
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => undefined;
  }
  const channel = new BroadcastChannel(VOICE_CONTEXT_CHANNEL);
  channel.onmessage = (message) => {
    if (isVoiceContextSnapshot(message.data)) {
      listener(message.data);
    }
  };
  return () => channel.close();
}

export type VoiceExecutionState = {
  isBusy: boolean;
  mode: VoiceContextSnapshot["mode"];
  sourceSessionId: string;
};

export function broadcastVoiceExecutionState(state: VoiceExecutionState): void {
  if (typeof BroadcastChannel === "undefined") {
    return;
  }
  const channel = new BroadcastChannel(VOICE_EXECUTION_CHANNEL);
  channel.postMessage(state);
  channel.close();
}

export function subscribeToVoiceExecutionState(
  listener: (state: VoiceExecutionState) => void
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => undefined;
  }
  const channel = new BroadcastChannel(VOICE_EXECUTION_CHANNEL);
  channel.onmessage = (message) => {
    if (isVoiceExecutionState(message.data)) {
      listener(message.data);
    }
  };
  return () => channel.close();
}

export type VoiceInterruptRequest = {
  type: "interrupt";
  sourceSessionId: string;
};

export function broadcastVoiceInterruptRequest(sourceSessionId: string): void {
  if (typeof BroadcastChannel === "undefined") {
    return;
  }
  const channel = new BroadcastChannel(VOICE_CONTROL_CHANNEL);
  channel.postMessage({ type: "interrupt", sourceSessionId } satisfies VoiceInterruptRequest);
  channel.close();
}

export function subscribeToVoiceInterruptRequests(
  listener: (request: VoiceInterruptRequest) => void
): () => void {
  if (typeof BroadcastChannel === "undefined") {
    return () => undefined;
  }
  const channel = new BroadcastChannel(VOICE_CONTROL_CHANNEL);
  channel.onmessage = (message) => {
    if (isVoiceInterruptRequest(message.data)) {
      listener(message.data);
    }
  };
  return () => channel.close();
}

function isVoiceContextSnapshot(value: unknown): value is VoiceContextSnapshot {
  if (!value || typeof value !== "object") {
    return false;
  }
  const snapshot = value as Partial<VoiceContextSnapshot>;
  return (
    (snapshot.mode === "sharedSession" ||
      snapshot.mode === "independentSession" ||
      snapshot.mode === "parallelWorktree") &&
    typeof snapshot.workspaceLabel === "string" &&
    typeof snapshot.branch === "string" &&
    typeof snapshot.sourceSessionId === "string" &&
    typeof snapshot.contextVersion === "number" &&
    Array.isArray(snapshot.contextMessages)
  );
}

function isVoiceExecutionState(value: unknown): value is VoiceExecutionState {
  if (!value || typeof value !== "object") {
    return false;
  }
  const state = value as Partial<VoiceExecutionState>;
  return (
    typeof state.isBusy === "boolean" &&
    (state.mode === "sharedSession" ||
      state.mode === "independentSession" ||
      state.mode === "parallelWorktree") &&
    typeof state.sourceSessionId === "string"
  );
}

function isVoiceInterruptRequest(value: unknown): value is VoiceInterruptRequest {
  if (!value || typeof value !== "object") {
    return false;
  }
  const request = value as Partial<VoiceInterruptRequest>;
  return request.type === "interrupt" && typeof request.sourceSessionId === "string";
}
