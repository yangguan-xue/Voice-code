import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { demoWorkspace } from "../../app/fixtures";

function renderComposer(onSubmit = vi.fn()) {
  render(
    <Composer
      workspace={demoWorkspace}
      permissionModes={["完全访问"]}
      branches={["main"]}
      isGitDirty={false}
      modelProfiles={[]}
      value="hello"
      isBusy={false}
      onChange={vi.fn()}
      onWorkspaceSelect={vi.fn()}
      onBranchChange={vi.fn()}
      onModelProfileChange={vi.fn()}
      onPermissionModeChange={vi.fn()}
      onSubmit={onSubmit}
      onVoiceOpen={vi.fn()}
      onInterrupt={vi.fn()}
    />
  );
  return onSubmit;
}

describe("Composer IME handling", () => {
  it("submits on Enter outside of IME composition", () => {
    const onSubmit = renderComposer();
    const input = screen.getByLabelText("输入任务");

    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("ignores the Enter that confirms an IME composition", () => {
    const onSubmit = renderComposer();
    const input = screen.getByLabelText("输入任务");

    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("ignores a trailing Enter that arrives right after compositionend (WKWebView order)", () => {
    vi.useFakeTimers();
    try {
      const onSubmit = renderComposer();
      const input = screen.getByLabelText("输入任务");

      fireEvent.compositionStart(input);
      fireEvent.compositionEnd(input);
      fireEvent.keyDown(input, { key: "Enter" });

      expect(onSubmit).not.toHaveBeenCalled();

      vi.advanceTimersByTime(200);
      fireEvent.keyDown(input, { key: "Enter" });

      expect(onSubmit).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores Enter with keyCode 229 from IMEs that skip composition events", () => {
    const onSubmit = renderComposer();
    const input = screen.getByLabelText("输入任务");

    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });

    expect(onSubmit).not.toHaveBeenCalled();
  });
});
