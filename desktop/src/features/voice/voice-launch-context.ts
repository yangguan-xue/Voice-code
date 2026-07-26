import type { ConversationState } from "../conversation/types";

export type VoiceLaunchMode = "sharedSession" | "independentSession" | "parallelWorktree";

export type VoiceContextMessage = {
  role: "user" | "assistant";
  content: string;
};

export type VoiceLaunchContext = {
  mode: VoiceLaunchMode;
  workspaceLabel: string;
  workspacePath: string;
  branch: string;
  sourceSessionId: string;
  sourceSessionTitle: string;
  contextVersion: number;
  contextMessages: VoiceContextMessage[];
};

const STORAGE_KEY = "voiceLaunchContext";
const LEGACY_STORAGE_KEY = "voiceWorkspaceContext";

export function writeVoiceLaunchContext(context: VoiceLaunchContext): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(context));
  window.localStorage.setItem(
    LEGACY_STORAGE_KEY,
    JSON.stringify({ workspaceLabel: context.workspaceLabel, branch: context.branch })
  );
}

export function readVoiceLaunchContext(): VoiceLaunchContext {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return readLegacyContext();
    }
    const parsed = JSON.parse(raw) as Partial<VoiceLaunchContext>;
    return {
      mode: normalizeLaunchMode(parsed.mode),
      workspaceLabel:
        typeof parsed.workspaceLabel === "string" ? parsed.workspaceLabel : "本地项目",
      workspacePath: typeof parsed.workspacePath === "string" ? parsed.workspacePath : "",
      branch: typeof parsed.branch === "string" ? parsed.branch : "main",
      sourceSessionId:
        typeof parsed.sourceSessionId === "string" ? parsed.sourceSessionId : "",
      sourceSessionTitle:
        typeof parsed.sourceSessionTitle === "string" ? parsed.sourceSessionTitle : "",
      contextVersion:
        typeof parsed.contextVersion === "number" && Number.isFinite(parsed.contextVersion)
          ? parsed.contextVersion
          : 0,
      contextMessages: sanitizeContextMessages(parsed.contextMessages)
    };
  } catch {
    return readLegacyContext();
  }
}

export function buildVoiceContextMessages(conversation: ConversationState): VoiceContextMessage[] {
  const messages: VoiceContextMessage[] = [];
  for (const turn of conversation.turns) {
    const userText = turn.userText.trim();
    if (userText) {
      messages.push({ role: "user", content: userText });
    }

    const assistantText = turn.assistantText.trim();
    if (assistantText) {
      messages.push({ role: "assistant", content: assistantText });
    }
  }
  return messages;
}

function readLegacyContext(): VoiceLaunchContext {
  try {
    const raw = window.localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) {
      return emptyContext();
    }
    const parsed = JSON.parse(raw) as Partial<{ workspaceLabel: unknown; branch: unknown }>;
    return {
      ...emptyContext(),
      workspaceLabel: typeof parsed.workspaceLabel === "string" ? parsed.workspaceLabel : "本地项目",
      branch: typeof parsed.branch === "string" ? parsed.branch : "main"
    };
  } catch {
    return emptyContext();
  }
}

function emptyContext(): VoiceLaunchContext {
  return {
    mode: "independentSession",
    workspaceLabel: "本地项目",
    workspacePath: "",
    branch: "main",
    sourceSessionId: "",
    sourceSessionTitle: "",
    contextVersion: 0,
    contextMessages: []
  };
}

function normalizeLaunchMode(mode: unknown): VoiceLaunchMode {
  switch (mode) {
    case "sharedSession":
    case "sharedContext":
      return "sharedSession";
    case "parallelWorktree":
      return "parallelWorktree";
    case "independent":
    case "independentSession":
    default:
      return "independentSession";
  }
}

function sanitizeContextMessages(value: unknown): VoiceContextMessage[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((message) => {
    if (!message || typeof message !== "object") {
      return [];
    }
    const candidate = message as Partial<VoiceContextMessage>;
    const role = candidate.role === "assistant" ? "assistant" : "user";
    const content = typeof candidate.content === "string" ? candidate.content.trim() : "";
    return content ? [{ role, content }] : [];
  });
}
