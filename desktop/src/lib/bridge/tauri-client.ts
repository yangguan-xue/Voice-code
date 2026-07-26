import { invoke } from "@tauri-apps/api/core";
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
import { WebSocketBridgeClient } from "./websocket-client";

type EmitBridgeEvent = (event: BridgeEvent) => void;

type DesktopBridgeConfig = {
  url: string;
  token: string;
  sessionId: string;
  cwd: string;
  model: string;
};

type StoredModelConfigList = {
  configs: ModelProfileSummary[];
  activeConfigId: string | null;
};

export function isTauriRuntime(): boolean {
  return Boolean(window.__TAURI_INTERNALS__);
}

export class TauriBridgeClient implements DesktopBridgeClient {
  private client: DesktopBridgeClient | null = null;
  private opening: Promise<DesktopBridgeClient> | null = null;
  private customConfigIds = new Set<string>();

  constructor(private readonly emit: EmitBridgeEvent) {}

  async connect(): Promise<void> {
    const client = await this.ensureClient();
    await client.connect();
  }

  async bootstrap(): Promise<BootstrapResult> {
    const [metadata, stored] = await Promise.all([
      invoke<BootstrapResult>("desktop_metadata"),
      invoke<StoredModelConfigList>("list_model_configs")
    ]);
    this.customConfigIds = new Set(stored.configs.map((config) => config.id));
    return {
      ...metadata,
      activeProfile: stored.activeConfigId || metadata.activeProfile,
      modelProfiles: [
        ...metadata.modelProfiles.map((profile) => ({ ...profile, source: "builtin" as const })),
        ...stored.configs
      ]
    };
  }

  async archiveSession(sessionId: string): Promise<boolean> {
    return (await this.ensureClient()).archiveSession(sessionId);
  }

  async createSession(): Promise<{ sessionId: string; workspacePath: string }> {
    return (await this.ensureClient()).createSession();
  }

  async resumeSession(sessionId: string): Promise<SessionResumeResult> {
    return (await this.ensureClient()).resumeSession(sessionId);
  }

  async checkoutBranch(branch: string): Promise<{ branch: string; branches: string[]; isDirty: boolean }> {
    return (await this.ensureClient()).checkoutBranch(branch);
  }

  async selectModelProfile(profile: string): Promise<{ profile: string; modelName: string }> {
    if (this.customConfigIds.has(profile)) {
      const config = await invoke<ModelConfigInput & { id: string }>("resolve_model_config", { id: profile });
      await invoke("set_active_model_config", { id: profile });
      const result = await (await this.ensureClient()).configureModel(config);
      return result;
    }
    await invoke("set_active_model_config", { id: null });
    const result = await (await this.ensureClient()).selectModelProfile(profile);
    return result;
  }

  async configureModel(config: ModelConfigInput & { id: string }): Promise<{ profile: string; modelName: string }> {
    return (await this.ensureClient()).configureModel(config);
  }

  async saveModelConfig(input: ModelConfigInput): Promise<ModelProfileSummary> {
    const saved = await invoke<ModelProfileSummary>("save_model_config", { input });
    this.customConfigIds.add(saved.id);
    return saved;
  }

  async deleteModelConfig(id: string): Promise<boolean> {
    const deleted = await invoke<boolean>("delete_model_config", { id });
    if (deleted) {
      this.customConfigIds.delete(id);
    }
    return deleted;
  }

  async loadVoiceSettings(): Promise<VoiceSettings> {
    return invoke<VoiceSettings>("load_voice_settings");
  }

  async saveVoiceSettings(input: VoiceSettingsInput): Promise<VoiceSettings> {
    return invoke<VoiceSettings>("save_voice_settings", { input });
  }

  async startTurn(text: string, options?: TurnStartOptions): Promise<TurnStartResult> {
    return (await this.ensureClient()).startTurn(text, options);
  }

  async interruptTurn(turnId?: number): Promise<boolean> {
    return (await this.ensureClient()).interruptTurn(turnId);
  }

  async resolvePermission(payload: PermissionResolvePayload): Promise<boolean> {
    return (await this.ensureClient()).resolvePermission(payload);
  }

  close(): void {
    this.client?.close();
    this.client = null;
    this.opening = null;
  }

  private async ensureClient(): Promise<DesktopBridgeClient> {
    if (this.client) {
      return this.client;
    }
    if (this.opening) {
      return this.opening;
    }

    this.opening = invoke<DesktopBridgeConfig>("ensure_desktop_bridge")
      .then(validateBridgeConfig)
      .then((config) => {
        const client = new WebSocketBridgeClient({
          url: config.url,
          token: config.token,
          emit: this.emit
        });
        this.client = client;
        this.opening = null;
        return client;
      })
      .catch((error: unknown) => {
        this.opening = null;
        throw error;
      });

    return this.opening;
  }
}

function validateBridgeConfig(config: DesktopBridgeConfig): DesktopBridgeConfig {
  if (!config.url || !config.token) {
    throw new Error("Desktop bridge config is missing url or token.");
  }
  return config;
}
