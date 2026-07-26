import { ArrowUp, Loader2 } from "lucide-react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";

type ComposerProps = {
  value: string;
  isBusy: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
};

export function Composer({ value, isBusy, disabled, onChange, onSubmit }: ComposerProps) {
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
      <textarea
        aria-label="输入任务"
        value={value}
        disabled={disabled}
        rows={3}
        placeholder="输入要交给 agent 的任务..."
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button type="submit" className="send-button" disabled={disabled || !value.trim()}>
        {isBusy ? (
          <Loader2 size={19} strokeWidth={1.9} className="spin" aria-hidden="true" />
        ) : (
          <ArrowUp size={19} strokeWidth={2} aria-hidden="true" />
        )}
        <span>{isBusy ? "执行中" : "发送"}</span>
      </button>
    </form>
  );
}
