export type WorkspaceSummary = {
  label: string;
  path: string;
  branch: string;
  locality: string;
  permissionMode: string;
  model: string;
};

export type SessionSummary = {
  id: string;
  title: string;
  isActive?: boolean;
  isEmpty?: boolean;
};

export type SessionGroup = {
  id: string;
  folderName: string;
  workspacePath?: string;
  sessions: SessionSummary[];
};

export const demoWorkspace: WorkspaceSummary = {
  label: "voice-code",
  path: "/Users/example/workspace/voice-code",
  branch: "main",
  locality: "本地",
  permissionMode: "完全访问",
  model: "5.5 高"
};

export const demoSessions: SessionGroup[] = [
  {
    id: "local",
    folderName: "voice-code",
    workspacePath: demoWorkspace.path,
    sessions: []
  }
];
