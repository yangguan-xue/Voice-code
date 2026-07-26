import type {
  BridgeEvent,
  BootstrapResult,
  DesktopBridgeClient,
  ModelConfigInput,
  ModelProfileSummary,
  PermissionResolvePayload,
  SessionResumeResult,
  TurnStartOptions,
  TurnStartResult,
  VoiceSettings,
  VoiceSettingsInput
} from "./protocol";

type EmitBridgeEvent = (event: BridgeEvent) => void;

export class MockBridgeClient implements DesktopBridgeClient {
  private nextTurnId = 1;
  private modelConfigs: ModelProfileSummary[] = [];
  private voiceSettings: VoiceSettings = {
    provider: "custom",
    sttUrl: "http://localhost:8765",
    ttsUrl: "http://localhost:8775",
    stepfunVoice: "cixingnansheng",
    stepfunKey: "",
    hasStepfunKey: false,
    ttsEnabled: false
  };

  constructor(
    private readonly emit: EmitBridgeEvent,
    private readonly sessionId = "mock-session"
  ) {}

  async connect(): Promise<void> {
    return Promise.resolve();
  }

  async bootstrap(): Promise<BootstrapResult> {
    return {
      sessionId: this.sessionId,
      sessionGroups: [
        {
          id: "/Users/example/workspace/voice-code",
          folderName: "voice-code",
          workspacePath: "/Users/example/workspace/voice-code",
          sessions: [
            { id: "mock-history-1", title: "冒泡算法文档" },
            { id: "mock-history-2", title: "Windows 桌面构建" }
          ]
        },
        {
          id: "D:/program/workspace/programs/notes",
          folderName: "notes",
          workspacePath: "D:/program/workspace/programs/notes",
          sessions: [{ id: "mock-history-3", title: "整理笔记" }]
        }
      ],
      workspacePath: "/Users/example/workspace/voice-code",
      branch: "codex/mac-desktop-ui",
      branches: ["codex/mac-desktop-ui", "main"],
      isDirty: false,
      activeProfile: "deepseek",
      modelProfiles: [
        { id: "deepseek", label: "deepseek", modelName: "deepseek-v4-pro" },
        { id: "local_openai", label: "local_openai", modelName: "your-local-model" }
      ]
    };
  }

  async archiveSession(_sessionId: string): Promise<boolean> {
    return true;
  }

  async createSession(): Promise<{ sessionId: string; workspacePath: string }> {
    return {
      sessionId: `mock-session-${Date.now()}`,
      workspacePath: "/Users/example/workspace/voice-code"
    };
  }

  async resumeSession(sessionId: string): Promise<SessionResumeResult> {
    return {
      sessionId,
      workspacePath: "/Users/example/workspace/voice-code",
      branch: "codex/mac-desktop-ui",
      branches: ["codex/mac-desktop-ui", "main"],
      isDirty: false,
      conversation: {
        activeTurnId: null,
        turns: [
          {
            id: 1,
            sessionId,
            userText: "恢复的历史任务",
            assistantText: "这是一段已恢复的历史回复。",
            reasoningText: "",
            status: "completed",
            finishReason: "completed",
            tools: [],
            errorText: ""
          }
        ]
      }
    };
  }

  async checkoutBranch(branch: string): Promise<{ branch: string; branches: string[]; isDirty: boolean }> {
    return { branch, branches: [branch, "main"], isDirty: false };
  }

  async selectModelProfile(profile: string): Promise<{ profile: string; modelName: string }> {
    return {
      profile,
      modelName: profile === "deepseek" ? "deepseek-v4-pro" : "your-local-model"
    };
  }

  async configureModel(config: ModelConfigInput & { id: string }): Promise<{ profile: string; modelName: string }> {
    return { profile: config.id, modelName: config.modelName };
  }

  async saveModelConfig(input: ModelConfigInput): Promise<ModelProfileSummary> {
    const id = input.id || `custom-${Date.now()}`;
    const config: ModelProfileSummary = {
      id,
      label: input.displayName.trim(),
      baseUrl: input.baseUrl.trim(),
      modelName: input.modelName.trim(),
      hasApiKey: Boolean(input.apiKey) || Boolean(this.modelConfigs.find((item) => item.id === id)?.hasApiKey),
      source: "custom"
    };
    this.modelConfigs = [...this.modelConfigs.filter((item) => item.id !== id), config];
    return config;
  }

  async deleteModelConfig(id: string): Promise<boolean> {
    const existed = this.modelConfigs.some((item) => item.id === id);
    this.modelConfigs = this.modelConfigs.filter((item) => item.id !== id);
    return existed;
  }

  async loadVoiceSettings(): Promise<VoiceSettings> {
    return this.voiceSettings;
  }

  async saveVoiceSettings(input: VoiceSettingsInput): Promise<VoiceSettings> {
    const nextKey = input.stepfunKey.trim();
    this.voiceSettings = {
      provider: input.provider,
      sttUrl: input.sttUrl.trim(),
      ttsUrl: input.ttsUrl.trim(),
      stepfunVoice: input.stepfunVoice.trim(),
      stepfunKey: "",
      hasStepfunKey: nextKey.length > 0 || this.voiceSettings.hasStepfunKey,
      ttsEnabled: input.ttsEnabled
    };
    return this.voiceSettings;
  }

  async startTurn(text: string, _options?: TurnStartOptions): Promise<TurnStartResult> {
    const turnId = this.nextTurnId;
    this.nextTurnId += 1;

    const events: BridgeEvent[] = [
      {
        type: "event",
        event: "agent.turn.started",
        sessionId: this.sessionId,
        turnId,
        payload: { userText: text }
      },
      {
        type: "event",
        event: "agent.reasoning.delta",
        sessionId: this.sessionId,
        turnId,
        payload: { content: "先确认项目结构和桌面入口。" }
      },
      {
        type: "event",
        event: "agent.tool.call",
        sessionId: this.sessionId,
        turnId,
        payload: {
          toolCallId: "mock-tool-1",
          toolName: "grep",
          toolArgs: { pattern: "desktop" }
        }
      },
      {
        type: "event",
        event: "agent.tool.result",
        sessionId: this.sessionId,
        turnId,
        payload: {
          toolCallId: "mock-tool-1",
          toolResult: "docs/specs/mac-desktop-technical-spec.md",
          resultPreview: "docs/specs/mac-desktop-technical-spec.md"
        }
      },
      {
        type: "event",
        event: "agent.text.delta",
        sessionId: this.sessionId,
        turnId,
        payload: {
          content: [
            "我先看项目结构，然后确认桌面入口。",
            "",
            "```ts",
            'const desktop = "ready";',
            "```",
            "",
            "| 项目 | 状态 |",
            "| --- | --- |",
            "| Markdown | ok |"
          ].join("\n")
        }
      },
      {
        type: "event",
        event: "agent.turn.finish",
        sessionId: this.sessionId,
        turnId,
        payload: { finishReason: "completed" }
      }
    ];

    for (const event of events) {
      this.emit(event);
      await Promise.resolve();
    }

    return {
      sessionId: this.sessionId,
      turnId,
      finishReason: "completed"
    };
  }

  async interruptTurn(): Promise<boolean> {
    return false;
  }

  async resolvePermission(_payload: PermissionResolvePayload): Promise<boolean> {
    return true;
  }

  close(): void {
    // The mock client does not own external resources.
  }
}
