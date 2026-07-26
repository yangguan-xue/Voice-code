import type { VoiceMessage, VoiceState, VoiceUiEvent } from "./voice-types";

export function createInitialVoiceState(): VoiceState {
  return {
    status: "idle",
    statusText: "准备好了",
    messages: [],
    partialUserText: "",
    ttsMuted: false,
    isCapturing: false
  };
}

export function voiceReducer(state: VoiceState, event: VoiceUiEvent): VoiceState {
  switch (event.type) {
    case "voice.state":
      return {
        ...state,
        status: event.state,
        statusText: event.text ?? statusTextFor(event.state),
        isCapturing: event.state === "listening",
        partialUserText: event.state === "listening" ? state.partialUserText : "",
        messages:
          event.state === "idle" || event.state === "paused" || event.state === "error"
            ? finishStreamingAssistant(state.messages)
            : state.messages
      };
    case "voice.user.partial":
      return {
        ...state,
        status: "listening",
        statusText: "我在听",
        partialUserText: event.text,
        isCapturing: true
      };
    case "voice.user.final":
      return {
        ...state,
        status: "thinking",
        statusText: "正在处理",
        partialUserText: "",
        isCapturing: false,
        messages: [...state.messages, createMessage("user", event.text)]
      };
    case "voice.assistant.delta":
      return {
        ...state,
        status: state.status === "speaking" ? "speaking" : "thinking",
        statusText: state.status === "speaking" ? "正在播报" : "正在处理",
        messages: appendAssistantDelta(state.messages, event.text)
      };
    case "voice.assistant.final":
      return {
        ...state,
        status: "idle",
        statusText: "准备好了",
        messages: finalizeAssistantMessage(state.messages, event.text)
      };
    case "voice.error":
      return {
        ...state,
        status: "error",
        statusText: event.recoverable ? "需要处理一下" : "语音暂不可用",
        partialUserText: "",
        isCapturing: false,
        messages: [
          ...finishStreamingAssistant(state.messages),
          createMessage("error", event.message)
        ]
      };
    default:
      return state;
  }
}

export function toggleTtsMuted(state: VoiceState): VoiceState {
  return {
    ...state,
    ttsMuted: !state.ttsMuted
  };
}

export function clearVoiceTranscript(state: VoiceState): VoiceState {
  return {
    ...state,
    messages: [],
    partialUserText: ""
  };
}

function appendAssistantDelta(messages: VoiceMessage[], text: string): VoiceMessage[] {
  const last = messages[messages.length - 1];
  if (last?.role === "assistant" && last.isStreaming) {
    return [
      ...messages.slice(0, -1),
      {
        ...last,
        text: `${last.text}${text}`
      }
    ];
  }

  return [...messages, createMessage("assistant", text, true)];
}

function finishStreamingAssistant(messages: VoiceMessage[]): VoiceMessage[] {
  return messages.map((message) =>
    message.role === "assistant" && message.isStreaming
      ? { ...message, isStreaming: false }
      : message
  );
}

function finalizeAssistantMessage(messages: VoiceMessage[], text: string): VoiceMessage[] {
  const last = messages[messages.length - 1];
  if (last?.role === "assistant" && last.isStreaming) {
    return [
      ...messages.slice(0, -1),
      {
        ...last,
        text,
        isStreaming: false
      }
    ];
  }

  return [...messages, createMessage("assistant", text)];
}

function createMessage(
  role: VoiceMessage["role"],
  text: string,
  isStreaming = false
): VoiceMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    text,
    isStreaming
  };
}

function statusTextFor(status: VoiceState["status"]): string {
  switch (status) {
    case "idle":
      return "准备好了";
    case "listening":
      return "我在听";
    case "transcribing":
      return "正在识别";
    case "thinking":
      return "正在处理";
    case "speaking":
      return "正在播报";
    case "paused":
      return "已暂停";
    case "error":
      return "需要处理一下";
  }
}
