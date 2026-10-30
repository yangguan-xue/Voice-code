const TOKEN_KEY = "voice-code.web-demo.tenant-token";

export function loadTenantToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveTenantToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // 隐私模式等场景下 localStorage 不可用：登录态退化为本次会话内有效
  }
}

export function clearTenantToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 同上
  }
}
