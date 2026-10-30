import { invoke } from "@tauri-apps/api/core";

type FrontendDiagnosticDetails = {
  kind?: string;
  source?: string | null;
  line?: number | null;
  column?: number | null;
};

export function installFrontendDiagnostics(): void {
  window.addEventListener("error", (event) => {
    const target = event.target;
    const resource =
      target && target !== window
        ? (target as { src?: string; href?: string; tagName?: string }).src ??
          (target as { src?: string; href?: string; tagName?: string }).href ??
          (target as { src?: string; href?: string; tagName?: string }).tagName
        : null;
    reportFrontendError(event.error ?? event.message, {
      kind: resource ? "resource" : "runtime",
      source: resource ?? event.filename ?? null,
      line: event.lineno || null,
      column: event.colno || null
    });
  });

  window.addEventListener("unhandledrejection", (event) => {
    reportFrontendError(event.reason, { kind: "unhandled-rejection" });
  });
}

export function reportFrontendError(
  error: unknown,
  details: FrontendDiagnosticDetails = {}
): void {
  const message = errorText(error);
  if (!window.__TAURI_INTERNALS__) {
    return;
  }

  void invoke("debug_frontend_error", {
    kind: details.kind ?? "runtime",
    message,
    source: details.source ?? null,
    line: details.line ?? null,
    column: details.column ?? null
  }).catch(() => undefined);
}

function errorText(error: unknown): string {
  if (error instanceof Error) {
    return error.stack || error.message || "Unknown frontend error";
  }
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  try {
    return JSON.stringify(error) || "Unknown frontend error";
  } catch {
    return "Unknown frontend error";
  }
}
