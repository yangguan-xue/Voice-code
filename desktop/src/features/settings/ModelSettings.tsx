import { AudioLines, Bot, Plus, Settings, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type {
  ModelConfigInput,
  ModelProfileSummary,
  VoiceConfigInput,
  VoiceConfigSummary
} from "../../lib/bridge/protocol";
import { DEFAULT_VOICE_CONFIG } from "../../lib/bridge/protocol";

type SettingsTab = "model" | "voice";

type ModelSettingsProps = {
  configs: ModelProfileSummary[];
  voiceConfig: VoiceConfigSummary | null;
  onClose: () => void;
  onSave: (input: ModelConfigInput) => Promise<ModelProfileSummary>;
  onDelete: (id: string) => Promise<boolean>;
  onVoiceSave: (input: VoiceConfigInput) => Promise<VoiceConfigSummary>;
};

const EMPTY_MODEL_FORM: ModelConfigInput = {
  displayName: "",
  baseUrl: "",
  apiKey: "",
  modelName: ""
};

function voiceFormFromSummary(config: VoiceConfigSummary | null): VoiceConfigInput {
  const summary = config ?? DEFAULT_VOICE_CONFIG;
  return {
    provider: summary.provider,
    sttUrl: summary.sttUrl,
    ttsUrl: summary.ttsUrl,
    apiKey: "",
    stepfunApiKey: "",
    stepfunVoice: summary.stepfunVoice,
    stepfunBaseUrl: summary.stepfunBaseUrl
  };
}

export function ModelSettings({
  configs,
  voiceConfig,
  onClose,
  onSave,
  onDelete,
  onVoiceSave
}: ModelSettingsProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>("model");
  const [selectedId, setSelectedId] = useState<string | null>(configs[0]?.id ?? null);
  const [form, setForm] = useState<ModelConfigInput>(EMPTY_MODEL_FORM);
  const [voiceForm, setVoiceForm] = useState<VoiceConfigInput>(() =>
    voiceFormFromSummary(voiceConfig)
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [voiceNotice, setVoiceNotice] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isVoiceSaving, setIsVoiceSaving] = useState(false);

  useEffect(() => {
    const selected = configs.find((config) => config.id === selectedId);
    if (!selected) {
      setForm(EMPTY_MODEL_FORM);
      return;
    }
    setForm({
      id: selected.id,
      displayName: selected.label,
      baseUrl: selected.baseUrl ?? "",
      apiKey: "",
      modelName: selected.modelName
    });
  }, [configs, selectedId]);

  useEffect(() => {
    setVoiceForm(voiceFormFromSummary(voiceConfig));
  }, [voiceConfig]);

  function update(key: keyof ModelConfigInput, value: string) {
    setNotice("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateVoice<K extends keyof VoiceConfigInput>(key: K, value: VoiceConfigInput[K]) {
    setVoiceNotice("");
    setVoiceForm((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setError("");
    setNotice("");
    setIsSaving(true);
    try {
      const saved = await onSave(form);
      setSelectedId(saved.id);
      setNotice(`已启用：${saved.label} (${saved.modelName})`);
    } catch (saveError: unknown) {
      setError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setIsSaving(false);
    }
  }

  async function saveVoice() {
    setVoiceError("");
    setVoiceNotice("");
    setIsVoiceSaving(true);
    try {
      const saved = await onVoiceSave(voiceForm);
      setVoiceNotice(`已启用：${voiceProviderLabel(saved.provider)}`);
    } catch (saveError: unknown) {
      setVoiceError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setIsVoiceSaving(false);
    }
  }

  async function remove() {
    if (!selectedId) {
      return;
    }
    setError("");
    setNotice("");
    try {
      const deleted = await onDelete(selectedId);
      if (deleted) {
        setSelectedId(null);
        setForm(EMPTY_MODEL_FORM);
      }
    } catch (deleteError: unknown) {
      setError(deleteError instanceof Error ? deleteError.message : String(deleteError));
    }
  }

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="settings-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="设置"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="settings-header">
          <div className="settings-title">
            <Settings size={18} strokeWidth={1.8} />
            <h2>设置</h2>
          </div>
          <button className="settings-icon-button" type="button" aria-label="关闭设置" onClick={onClose}>
            <X size={18} strokeWidth={1.8} />
          </button>
        </header>

        <div className="settings-tabs" role="tablist" aria-label="设置分类">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "model"}
            className={activeTab === "model" ? "settings-tab-active" : ""}
            onClick={() => setActiveTab("model")}
          >
            <Bot size={16} strokeWidth={1.8} />
            模型
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "voice"}
            className={activeTab === "voice" ? "settings-tab-active" : ""}
            onClick={() => setActiveTab("voice")}
          >
            <AudioLines size={16} strokeWidth={1.8} />
            语音
          </button>
        </div>

        {activeTab === "model" ? (
          <div className="settings-content" role="tabpanel" aria-label="模型">
            <aside className="model-config-list" aria-label="已保存的模型配置">
              <button
                className={`model-config-new ${selectedId === null ? "model-config-active" : ""}`}
                type="button"
                onClick={() => setSelectedId(null)}
              >
                <Plus size={16} strokeWidth={1.8} />
                新增配置
              </button>
              {configs.map((config) => (
                <button
                  className={`model-config-row ${selectedId === config.id ? "model-config-active" : ""}`}
                  type="button"
                  key={config.id}
                  onClick={() => setSelectedId(config.id)}
                >
                  <strong>{config.label}</strong>
                  <span>{config.modelName}</span>
                </button>
              ))}
            </aside>

            <form className="model-config-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
              <label>
                <span>显示名称</span>
                <input
                  aria-label="显示名称"
                  value={form.displayName}
                  onChange={(event) => update("displayName", event.target.value)}
                  placeholder="例如：公司 GPT"
                  required
                />
              </label>
              <label>
                <span>API 地址</span>
                <input
                  aria-label="API 地址"
                  type="url"
                  value={form.baseUrl}
                  onChange={(event) => update("baseUrl", event.target.value)}
                  placeholder="https://api.example.com/v1"
                  required
                />
              </label>
              <label>
                <span>API Key</span>
                <input
                  aria-label="API Key"
                  type="password"
                  value={form.apiKey}
                  onChange={(event) => update("apiKey", event.target.value)}
                  placeholder={selectedId ? "留空则保留原密钥" : "输入 API Key"}
                  autoComplete="new-password"
                />
              </label>
              <label>
                <span>模型名称</span>
                <input
                  aria-label="模型名称"
                  value={form.modelName}
                  onChange={(event) => update("modelName", event.target.value)}
                  placeholder="例如：gpt-5.1"
                  required
                />
              </label>

              {error ? <p className="model-config-error" role="alert">{error}</p> : null}
              {notice ? (
                <p className="model-config-notice" role="status" aria-label="模型配置状态">
                  {notice}
                </p>
              ) : null}
              <div className="model-config-actions">
                {selectedId ? (
                  <button className="settings-delete-button" type="button" aria-label="删除配置" onClick={() => void remove()}>
                    <Trash2 size={16} strokeWidth={1.8} />
                    删除
                  </button>
                ) : <span />}
                <button className="settings-save-button" type="submit" aria-label="保存配置" disabled={isSaving}>
                  {isSaving ? "正在启用" : "保存并使用"}
                </button>
              </div>
            </form>
          </div>
        ) : (
          <div className="settings-content settings-content-single" role="tabpanel" aria-label="语音">
            <form className="model-config-form voice-config-form" onSubmit={(event) => { event.preventDefault(); void saveVoice(); }}>
              <label>
                <span>语音后端</span>
                <select
                  aria-label="语音后端"
                  value={voiceForm.provider}
                  onChange={(event) =>
                    updateVoice("provider", event.target.value === "stepfun" ? "stepfun" : "remote")
                  }
                >
                  <option value="remote">自建 STT/TTS 服务</option>
                  <option value="stepfun">Step Fun API</option>
                </select>
              </label>

              {voiceForm.provider === "remote" ? (
                <>
                  <label>
                    <span>语音 API Key</span>
                    <input
                      aria-label="语音 API Key"
                      type="password"
                      value={voiceForm.apiKey}
                      onChange={(event) => updateVoice("apiKey", event.target.value)}
                      placeholder={voiceConfig?.hasApiKey ? "留空则保留原密钥" : "可选，输入 API Key"}
                      autoComplete="new-password"
                    />
                  </label>
                  <label>
                    <span>STT 地址</span>
                    <input
                      aria-label="STT 地址"
                      type="url"
                      value={voiceForm.sttUrl}
                      onChange={(event) => updateVoice("sttUrl", event.target.value)}
                      placeholder="http://localhost:8765"
                      required
                    />
                  </label>
                  <label>
                    <span>TTS 地址</span>
                    <input
                      aria-label="TTS 地址"
                      type="url"
                      value={voiceForm.ttsUrl}
                      onChange={(event) => updateVoice("ttsUrl", event.target.value)}
                      placeholder="http://localhost:8775"
                      required
                    />
                  </label>
                </>
              ) : (
                <>
                  <label>
                    <span>Step Fun API Key</span>
                    <input
                      aria-label="Step Fun API Key"
                      type="password"
                      value={voiceForm.stepfunApiKey}
                      onChange={(event) => updateVoice("stepfunApiKey", event.target.value)}
                      placeholder={voiceConfig?.hasApiKey ? "留空则保留原密钥" : "输入 API Key"}
                      autoComplete="new-password"
                    />
                  </label>
                  <label>
                    <span>声音</span>
                    <input
                      aria-label="声音"
                      value={voiceForm.stepfunVoice}
                      onChange={(event) => updateVoice("stepfunVoice", event.target.value)}
                      placeholder="cixingnansheng"
                      required
                    />
                  </label>
                  <label>
                    <span>服务链接</span>
                    <input
                      aria-label="Step Fun 服务链接"
                      type="url"
                      value={voiceForm.stepfunBaseUrl}
                      onChange={(event) => updateVoice("stepfunBaseUrl", event.target.value)}
                      placeholder="https://api.stepfun.com/step_plan/v1"
                    />
                  </label>
                </>
              )}

              {voiceError ? <p className="model-config-error" role="alert">{voiceError}</p> : null}
              {voiceNotice ? (
                <p className="model-config-notice" role="status" aria-label="语音配置状态">
                  {voiceNotice}
                </p>
              ) : null}
              <div className="model-config-actions">
                <span />
                <button
                  className="settings-save-button"
                  type="submit"
                  aria-label="保存语音配置"
                  disabled={isVoiceSaving}
                >
                  {isVoiceSaving ? "正在保存" : "保存语音配置"}
                </button>
              </div>
            </form>
          </div>
        )}
      </section>
    </div>
  );
}

function voiceProviderLabel(provider: VoiceConfigSummary["provider"]): string {
  return provider === "stepfun" ? "Step Fun" : "自建语音服务";
}
