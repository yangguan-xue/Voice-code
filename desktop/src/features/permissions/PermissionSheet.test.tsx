import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PermissionSheet } from "./PermissionSheet";

describe("PermissionSheet", () => {
  it("renders permission details and resolves each decision", () => {
    const onResolve = vi.fn();
    render(
      <PermissionSheet
        toolName="write"
        reason="Non-readonly tools require approval by default."
        onResolve={onResolve}
      />
    );

    expect(screen.getByRole("dialog", { name: "允许 agent 使用工具？" })).toBeInTheDocument();
    expect(screen.getByText("write")).toBeInTheDocument();
    expect(screen.getByText("Non-readonly tools require approval by default.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    fireEvent.click(screen.getByRole("button", { name: "允许一次" }));
    fireEvent.click(screen.getByRole("button", { name: "本会话始终允许" }));
    fireEvent.click(screen.getByRole("button", { name: "信任当前工作区" }));

    expect(onResolve).toHaveBeenNthCalledWith(1, "deny", "");
    expect(onResolve).toHaveBeenNthCalledWith(2, "allow", "");
    expect(onResolve).toHaveBeenNthCalledWith(3, "allow", "session");
    expect(onResolve).toHaveBeenNthCalledWith(4, "allow", "workspace");
  });
});
