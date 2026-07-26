import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DiffPanel } from "./DiffPanel";

describe("DiffPanel", () => {
  it("enables source download when exactly one changed file exists", () => {
    const onDownloadSource = vi.fn();

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
        onDownloadSource={onDownloadSource}
      />
    );

    const button = screen.getByRole("button", { name: "下载源文件" });
    expect(button).toBeEnabled();

    fireEvent.click(button);
    expect(onDownloadSource).toHaveBeenCalledTimes(1);
  });
});
