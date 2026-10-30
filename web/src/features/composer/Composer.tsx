import { ArrowUp, Loader2, Paperclip, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";

export type ComposerAttachment = {
  id: string;
  file: File;
};

type ComposerProps = {
  value: string;
  isBusy: boolean;
  disabled: boolean;
  attachments: ComposerAttachment[];
  onChange: (value: string) => void;
  onSubmit: () => void;
  onAttach: (files: File[]) => void;
  onRemoveAttachment: (id: string) => void;
};

export function Composer({
  value,
  isBusy,
  disabled,
  attachments,
  onChange,
  onSubmit,
  onAttach,
  onRemoveAttachment
}: ComposerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea like a chat composer: one line by default,
  // expands with content up to a cap, then scrolls internally.
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "auto";
    const capped = Math.min(textarea.scrollHeight, 132);
    textarea.style.height = `${capped}px`;
    textarea.style.overflowY = textarea.scrollHeight > 132 ? "auto" : "hidden";
  }, [value]);
  const canSubmit = Boolean(value.trim()) || attachments.length > 0;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit();
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    const nativeEvent = event.nativeEvent as KeyboardEvent & { isComposing?: boolean };
    if (event.key !== "Enter" || event.shiftKey || nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    onSubmit();
  }

  return (
    <form className="composer" aria-label="任务输入" onSubmit={handleSubmit}>
      {attachments.length > 0 ? (
        <div className="attachment-chips" aria-label="待上传附件">
          {attachments.map((attachment) => (
            <span key={attachment.id} className="attachment-chip">
              {attachment.file.name}
              <button
                type="button"
                className="attachment-chip-remove"
                aria-label={`移除 ${attachment.file.name}`}
                onClick={() => onRemoveAttachment(attachment.id)}
              >
                <X size={12} strokeWidth={2} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="composer-row">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(event) => {
            onAttach(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
        <button
          type="button"
          className="attach-button"
          aria-label="添加图片附件"
          disabled={disabled}
          onClick={() => fileInputRef.current?.click()}
        >
          <Paperclip size={18} strokeWidth={1.8} aria-hidden="true" />
        </button>
        <textarea
          ref={textareaRef}
          aria-label="输入任务"
          value={value}
          disabled={disabled}
          rows={1}
          placeholder="给 agent 派个任务…"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button type="submit" className="send-button" disabled={disabled || !canSubmit}>
          {isBusy ? (
            <Loader2 size={19} strokeWidth={1.9} className="spin" aria-hidden="true" />
          ) : (
            <ArrowUp size={19} strokeWidth={2} aria-hidden="true" />
          )}
          <span>{isBusy ? "执行中" : "发送"}</span>
        </button>
      </div>
    </form>
  );
}
