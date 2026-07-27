import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  it("renders inline code, fenced code blocks, tables, and safe links", () => {
    render(
      <MarkdownMessage
        text={[
          "这里有 `inline_code`。",
          "",
          "```ts",
          "const value = 42;",
          "```",
          "",
          "| 名称 | 状态 |",
          "| --- | --- |",
          "| markdown | ok |",
          "",
          "[OpenAI](https://openai.com)"
        ].join("\n")}
      />
    );

    expect(screen.getByText("inline_code")).toHaveClass("markdown-inline-code");
    expect(screen.getByText("const value = 42;")).toBeInTheDocument();
    expect(screen.getByText("ts")).toHaveClass("markdown-code-language");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "markdown" })).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "OpenAI" });
    expect(link).toHaveAttribute("href", "https://openai.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
  });

  it("does not render raw html from model output", () => {
    render(<MarkdownMessage text={'<img src=x onerror="alert(1)"> <script>alert(1)</script>'} />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByText("alert(1)")).not.toBeInTheDocument();
  });

  it("keeps long code inside a scrollable code surface", () => {
    render(<MarkdownMessage text={"```bash\npython -c 'print(\"x\" * 200)'\n```"} />);

    const code = screen.getByText("python -c 'print(\"x\" * 200)'");
    const surface = code.closest(".markdown-code-block");
    expect(surface).toBeInTheDocument();
    expect(within(surface as HTMLElement).getByText("bash")).toBeInTheDocument();
  });
});
