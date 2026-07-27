export type VoiceUiState =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "paused"
  | "error";

export type VoiceUiEvent =
  | { type: "voice.state"; state: VoiceUiState; text?: string; sessionId?: string; turnId?: number }
  | { type: "voice.user.partial"; text: string; sessionId?: string; turnId?: number }
  | { type: "voice.user.final"; text: string; sessionId?: string; turnId?: number }
  | { type: "voice.assistant.delta"; text: string; sessionId?: string; turnId?: number }
  | { type: "voice.assistant.final"; text: string; sessionId?: string; turnId?: number }
  | { type: "voice.error"; message: string; recoverable: boolean; sessionId?: string; turnId?: number };

export type VoiceMessageRole = "user" | "assistant" | "status" | "error";

export type VoiceMessage = {
  id: string;
  role: VoiceMessageRole;
  text: string;
  isStreaming?: boolean;
};

export type VoiceState = {
  status: VoiceUiState;
  statusText: string;
  messages: VoiceMessage[];
  partialUserText: string;
  ttsMuted: boolean;
  isCapturing: boolean;
};
