export type ThemeName = "dark" | "paper";

const STORAGE_KEY = "voice-code-theme";

export function getStoredTheme(): ThemeName {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "paper" ? "paper" : "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(theme: ThemeName): void {
  if (theme === "paper") {
    document.documentElement.dataset.theme = "paper";
  } else {
    delete document.documentElement.dataset.theme;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // 存储不可用时仅本次会话生效
  }
}
