import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DiffPanel } from "./DiffPanel";

describe("DiffPanel", () => {
  it("enables source download when exactly one changed file exists", () => {
    const onDownload = vi.fn();

    render(
      <DiffPanel
        diff={{
          changedFiles: [
            {
              path: "linux-sandboxing.md",
              status: "added",
              additions: 10,
              deletions: 0
            }
          ],
          patchAvailable: false,
          patch: "diff --git a/file b/file",
          diskUsageBytes: 128
        }}
        onDownload={onDownload}
      />
    );

    const button = screen.getByRole("button", { name: "下载源文件" });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    expect(onDownload).toHaveBeenCalledTimes(1);
  });

  it("enables patch download when multiple changed files are available", () => {
    const onDownload = vi.fn();

    render(
      <DiffPanel
        diff={{
          changedFiles: [
            { path: "src/app.py", status: "modified", additions: 2, deletions: 1 },
            { path: "tests/test_app.py", status: "modified", additions: 4, deletions: 0 }
          ],
          patchAvailable: true,
          patch: "diff --git a/src/app.py b/src/app.py",
          diskUsageBytes: 256
        }}
        onDownload={onDownload}
      />
    );

    const button = screen.getByRole("button", { name: "下载 patch" });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    expect(onDownload).toHaveBeenCalledTimes(1);
  });

  it("disables download when multiple changed files have no patch", () => {
    render(
      <DiffPanel
        diff={{
          changedFiles: [
            { path: "src/app.py", status: "modified", additions: 2, deletions: 1 },
            { path: "tests/test_app.py", status: "modified", additions: 4, deletions: 0 }
          ],
          patchAvailable: false,
          patch: "",
          diskUsageBytes: 256
        }}
        onDownload={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "暂无可下载内容" })).toBeDisabled();
  });
});
