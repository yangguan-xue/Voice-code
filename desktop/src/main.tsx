import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { WindowEntry } from "./app/window-entry";
import { installFrontendDiagnostics, reportFrontendError } from "./lib/desktop/frontend-diagnostics";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/voice.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Root element #root was not found");
}

installFrontendDiagnostics();

type AppErrorBoundaryState = {
  error: Error | null;
};

class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): AppErrorBoundaryState {
    return { error: toError(error) };
  }

  componentDidCatch(error: unknown, info: { componentStack?: string | null }): void {
    reportFrontendError(error, {
      kind: "react-render",
      source: info.componentStack ?? null
    });
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <main
        role="alert"
        style={{
          minHeight: "100vh",
          boxSizing: "border-box",
          padding: "32px",
          background: "#000",
          color: "#ebe7de",
          fontFamily: "system-ui, sans-serif"
        }}
      >
        <h1 style={{ margin: "0 0 12px", fontSize: "20px" }}>语码启动失败</h1>
        <p style={{ color: "#a59f95", lineHeight: 1.6 }}>
          页面发生异常，详细错误已写入本地 frontend.log。
        </p>
        <pre
          style={{
            maxWidth: "100%",
            overflow: "auto",
            padding: "12px",
            background: "#111",
            color: "#ffb1a8",
            whiteSpace: "pre-wrap"
          }}
        >
          {this.state.error.message}
        </pre>
      </main>
    );
  }
}

function toError(error: unknown): Error {
  if (error instanceof Error) {
    return error;
  }
  return new Error(typeof error === "string" ? error : "Unknown frontend error");
}

createRoot(root).render(
  <StrictMode>
    <AppErrorBoundary>
      <WindowEntry />
    </AppErrorBoundary>
  </StrictMode>
);
