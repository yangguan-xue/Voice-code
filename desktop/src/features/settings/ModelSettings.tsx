import { Plus, Settings, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type {
  ModelConfigInput,
  ModelProfileSummary,
  VoiceSettings,
  VoiceSettingsInput
} from "../../lib/bridge/protocol";

type ModelSettingsProps = {
  configs: ModelProfileSummary[];
  voiceSettings: VoiceSettings;
  onClose: () => void;
  onSave: (input: ModelConfigInput) => Promise<ModelProfileSummary>;
  onDelete: (id: string) => Promise<boolean>;
  onVoiceSave: (input: VoiceSettingsInput) => Promise<VoiceSettings>;
};

const EMPTY_MODEL_FORM: ModelConfigInput = {
  displayName: "",
  baseUrl: "",
  apiKey: "",
  modelName: ""
};

export function ModelSettings({
  configs,
  voiceSettings,
  onClose,
  onSave,
  onDelete,
  onVoiceSave
}: ModelSettingsProps) {
  const [selectedId, setSelectedId] = useState<string | null>(configs[0]?.id ?? null);
  const [modelForm, setModelForm] = useState<ModelConfigInput>(EMPTY_MODEL_FORM);
  const [voiceForm, setVoiceForm] = useState<VoiceSettingsInput>({
    provider: voiceSettings.provider,
    sttUrl: voiceSettings.sttUrl,
    ttsUrl: voiceSettings.ttsUrl,
    stepfunVoice: voiceSettings.stepfunVoice,
    stepfunKey: "",
    ttsEnabled: voiceSettings.ttsEnabled
  });
  const [hasStoredStepfunKey, setHasStoredStepfunKey] = useState(voiceSettings.hasStepfunKey);
  const [modelError, setModelError] = useState("");
  const [modelNotice, setModelNotice] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [voiceNotice, setVoiceNotice] = useState("");
  const [isSavingModel, setIsSavingModel] = useState(false);
  const [isSavingVoice, setIsSavingVoice] = useState(false);

  useEffect(() => {
    const selected = configs.find((config) => config.id === selectedId);
    if (!selected) {
      setModelForm(EMPTY_MODEL_FORM);
      return;
    }
    setModelForm({
      id: selected.id,
      displayName: selected.label,
      baseUrl: selected.baseUrl ?? "",
      apiKey: "",
      modelName: selected.modelName
    });
  }, [configs, selectedId]);

  useEffect(() => {
    setVoiceForm({
      provider: voiceSettings.provider,
      sttUrl: voiceSettings.sttUrl,
      ttsUrl: voiceSettings.ttsUrl,
      stepfunVoice: voiceSettings.stepfunVoice,
      stepfunKey: "",
      ttsEnabled: voiceSettings.ttsEnabled
    });
    setHasStoredStepfunKey(voiceSettings.hasStepfunKey);
  }, [voiceSettings]);

  function updateModel(key: keyof ModelConfigInput, value: string) {
    setModelNotice("");
    setModelForm((current) => ({ ...current, [key]: value }));
  }

  function updateVoice<K extends keyof VoiceSettingsInput>(key: K, value: VoiceSettingsInput[K]) {
    setVoiceNotice("");
    setVoiceForm((current) => ({ ...current, [key]: value }));
  }

  async function saveModel() {
    setModelError("");
    setModelNotice("");
    setIsSavingModel(true);
    try {
      const saved = await onSave(modelForm);
      setSelectedId(saved.id);
      setModelNotice(`已启用：${saved.label} (${saved.modelName})`);
    } catch (saveError: unknown) {
      setModelError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setIsSavingModel(false);
    }
  }

  async function removeModel() {
    if (!selectedId) {
      return;
    }
    setModelError("");
    setModelNotice("");
    try {
      const deleted = await onDelete(selectedId);
      if (deleted) {
        setSelectedId(null);
        setModelForm(EMPTY_MODEL_FORM);
      }
    } catch (deleteError: unknown) {
      setModelError(deleteError instanceof Error ? deleteError.message : String(deleteError));
    }
  }

  async function saveVoice() {
    setVoiceError("");
    setVoiceNotice("");
    setIsSavingVoice(true);
    try {
      const saved = await onVoiceSave(voiceForm);
      setVoiceForm((current) => ({
        ...current,
        provider: saved.provider,
        sttUrl: saved.sttUrl,
        ttsUrl: saved.ttsUrl,
        stepfunVoice: saved.stepfunVoice,
        stepfunKey: "",
        ttsEnabled: saved.ttsEnabled
      }));
      setHasStoredStepfunKey(saved.hasStepfunKey);
      setVoiceNotice(saved.provider === "stepfun" ? "StepFun 语音已保存并启用。" : "本地 STT / TTS 地址已保存。");
    } catch (saveError: unknown) {
      setVoiceError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setIsSavingVoice(false);
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

        <div className="settings-stack">
          <section className="settings-section" aria-labelledby="model-settings-title">
            <div className="settings-section-heading">
              <h3 id="model-settings-title">模型配置</h3>
              <p>管理桌面端默认使用的模型列表。</p>
            </div>

            <div className="settings-content">
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

              <form className="model-config-form" onSubmit={(event) => { event.preventDefault(); void saveModel(); }}>
                <label>
                  <span>显示名称</span>
                  <input
                    aria-label="显示名称"
                    value={modelForm.displayName}
                    onChange={(event) => updateModel("displayName", event.target.value)}
                    placeholder="例如：公司 GPT"
                    required
                  />
                </label>
                <label>
                  <span>API 地址</span>
                  <input
                    aria-label="API 地址"
                    type="url"
                    value={modelForm.baseUrl}
                    onChange={(event) => updateModel("baseUrl", event.target.value)}
                    placeholder="https://api.example.com/v1"
                    required
                  />
                </label>
                <label>
                  <span>API Key</span>
                  <input
                    aria-label="API Key"
                    type="password"
                    value={modelForm.apiKey}
                    onChange={(event) => updateModel("apiKey", event.target.value)}
                    placeholder={selectedId ? "留空则保留原密钥" : "输入 API Key"}
                    autoComplete="new-password"
                  />
                </label>
                <label>
                  <span>模型名称</span>
                  <input
                    aria-label="模型名称"
                    value={modelForm.modelName}
                    onChange={(event) => updateModel("modelName", event.target.value)}
                    placeholder="例如：gpt-5.1"
                    required
                  />
                </label>

                {modelError ? <p className="model-config-error" role="alert">{modelError}</p> : null}
                {modelNotice ? (
                  <p className="model-config-notice" role="status" aria-label="模型配置状态">
                    {modelNotice}
                  </p>
                ) : null}
                <div className="model-config-actions">
                  {selectedId ? (
                    <button className="settings-delete-button" type="button" aria-label="删除配置" onClick={() => void removeModel()}>
                      <Trash2 size={16} strokeWidth={1.8} />
                      删除
                    </button>
                  ) : <span />}
                  <button className="settings-save-button" type="submit" aria-label="保存配置" disabled={isSavingModel}>
                    {isSavingModel ? "正在启用" : "保存并使用"}
                  </button>
                </div>
              </form>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="voice-settings-title">
            <div className="settings-section-heading">
              <h3 id="voice-settings-title">语音配置</h3>
              <p>控制 Windows 语音窗口使用 StepFun 还是本地 STT / TTS 服务。</p>
            </div>

            <form className="voice-settings-form" onSubmit={(event) => { event.preventDefault(); void saveVoice(); }}>
              <label>
                <span>语音后端</span>
                <select
                  aria-label="语音后端"
                  value={voiceForm.provider}
                  onChange={(event) => updateVoice("provider", event.target.value as VoiceSettingsInput["provider"])}
                >
                  <option value="custom">本地 STT / TTS</option>
                  <option value="stepfun">StepFun</option>
                </select>
              </label>

              {voiceForm.provider === "stepfun" ? (
                <>
                  <label>
                    <span>StepFun Key</span>
                    <input
                      aria-label="StepFun Key"
                      type="password"
                      value={voiceForm.stepfunKey}
                      onChange={(event) => updateVoice("stepfunKey", event.target.value)}
                      placeholder={hasStoredStepfunKey ? "留空则保留原密钥" : "输入 StepFun Key"}
                      autoComplete="new-password"
                    />
                  </label>
                  <label>
                    <span>StepFun 音色</span>
                    <input
                      aria-label="StepFun 音色"
                      value={voiceForm.stepfunVoice}
                      onChange={(event) => updateVoice("stepfunVoice", event.target.value)}
                      placeholder="例如：cixingnansheng"
                      required
                    />
                  </label>
                </>
              ) : (
                <>
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
              )}

              <label className="voice-settings-toggle">
                <input
                  aria-label="启用语音播报"
                  type="checkbox"
                  checked={voiceForm.ttsEnabled}
                  onChange={(event) => updateVoice("ttsEnabled", event.target.checked)}
                />
                <span>启动时直接开启语音播报</span>
              </label>

              {voiceError ? <p className="model-config-error" role="alert">{voiceError}</p> : null}
              {voiceNotice ? (
                <p className="model-config-notice" role="status" aria-label="语音配置状态">
                  {voiceNotice}
                </p>
              ) : null}

              <div className="voice-settings-actions">
                <span className="voice-settings-hint">
                  语音窗口下次连接 bridge 时会自动使用这组配置。
                </span>
                <button className="settings-save-button" type="submit" aria-label="保存语音配置" disabled={isSavingVoice}>
                  {isSavingVoice ? "正在保存" : "保存语音配置"}
                </button>
              </div>
            </form>
          </section>
        </div>
      </section>
    </div>
  );
}
