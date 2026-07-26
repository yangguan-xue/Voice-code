# Spec: Mac Desktop Workspace Experience

## Assumptions
1. 第一版目标是 macOS 桌面应用，不是继续增强 Textual TUI，也不是先做网页登录产品。
2. 第一版不做登录、账号体系、云同步或远程团队协作。所有会话、权限和项目上下文都先走本地。
3. 截图中的 Codex 页面是信息架构和交互参考，不做品牌、文案或像素级复制。
4. 现有 Python agent 核心继续作为执行引擎，桌面页面只替代输入、会话浏览、权限审批和任务监控体验。
5. 用户主要是本机开发者，默认会在一个或多个代码工作区之间切换，界面语言先以中文为主。

## Objective
做一个真正的 mac 端页面，让语码从“终端里的 TUI agent”升级为“本地桌面工作台”。

用户应该能在一个窗口里完成这些事情：
- 选择或新建工作区。
- 查看按项目分组的历史任务。
- 发起新任务，并看到 agent 的流式输出、思考、工具调用和最终结果。
- 在页面里处理工具权限请求。
- 查看当前工作区、执行位置、git 分支、模型和权限模式。
- 中断正在执行的任务，或继续打开历史会话。

成功状态：用户不需要打开终端 TUI，也能获得和当前 `reasoning`/`reasoning-tui` 等价的核心能力，并且界面结构接近截图中的 Codex 桌面体验。

## Tech Stack
产品方向：
- macOS desktop shell: Tauri/WebView 风格的轻量桌面壳，具体版本在实现前 pin。
- Frontend: React + TypeScript + Vite，使用系统字体和 CSS design tokens。
- Icons: lucide-react 或等价图标库。
- Agent runtime: 现有 Python `voice_code`。
- Local transport: 本地 agent bridge，详见 [mac-desktop-technical-spec.md](mac-desktop-technical-spec.md)。

## Commands
当前项目质量门禁：

```bash
cd new
uv sync
uv run ruff check src/
uv run pytest tests/ -v
```

未来桌面开发命令目标：

```bash
cd new
uv run reasoning-desktop-bridge --workspace "$PWD" --profile deepseek
cd desktop
pnpm install
pnpm tauri dev
pnpm test
pnpm lint
pnpm build
pnpm tauri build
```

说明：`desktop/` 和 `reasoning-desktop-bridge` 目前还不存在，属于本 spec 的后续实现目标。

## Project Structure
目标目录：

```text
new/
  desktop/                         # mac desktop UI project
    src/
      app/                         # top-level route/layout/state wiring
      components/                  # focused UI components
      features/
        workspace/                 # workspace picker/sidebar groups
        sessions/                  # task/thread list and resume
        conversation/              # transcript canvas and turn renderer
        composer/                  # bottom prompt composer
        permissions/               # approval modal/sheet
        runtime/                   # model, branch, permission mode indicators
      lib/
        bridge/                    # typed local bridge client
        design-tokens.ts           # shared tokens for the UI
      styles/
        tokens.css
        app.css
    src-tauri/                     # Tauri shell, permissions, sidecar launch
    tests/
  src/voice_code/             # existing Python package
  docs/specs/
    mac-desktop-product-spec.md
    mac-desktop-technical-spec.md
```

## Product Architecture

### 1. Window Shell
The app uses a single persistent macOS window:
- Native titlebar/traffic lights remain visible.
- Left sidebar is always the primary navigation on desktop widths.
- Main canvas owns the active conversation.
- Bottom composer floats at the bottom center of the canvas.
- Permission prompts appear as modal sheets over the conversation.
- A right inspector is allowed later for tool details or task details, but it is not required for V0.

### 2. Left Sidebar
Reference from screenshot:
- App title: `语码` with a compact workspace/menu affordance.
- Search icon.
- `新建任务` button.
- Project groups by folder/workspace.
- Recent tasks under each project.
- Selected project row has a subtle filled highlight.
- Bottom area shows local user/settings/help entry.

V0 requirements:
- Show current workspace group.
- Show recent sessions using existing transcript/session metadata.
- New task creates a fresh session without closing the app.
- Search can be a visible control in V0, but full fuzzy search may be deferred.
- No login prompt or account switcher.

### 3. Main Conversation Canvas
Empty state:
- Centered prompt, for example: `我们应该在 new 中构建什么?`
- Small app glyph above the prompt.
- No marketing hero, no feature explanation panel, no onboarding cards.

Active state:
- User prompts render as compact turn blocks.
- Assistant output streams incrementally.
- Reasoning/thinking is visible while live, then collapsible after completion.
- Tool calls render as structured rows with tool name, status, args preview and result preview.
- Errors render inline with retry/context.

### 4. Bottom Composer
The composer is the main working surface:
- Top metadata row: workspace/folder, execution locality, git branch.
- Textarea with multiline input.
- Left controls: attachment/add context, permission mode.
- Right controls: model selector, mic button placeholder, send button.
- `Enter` sends; `Shift+Enter` inserts newline.
- Busy state disables send and enables interrupt.

No visible help text should explain every control. Unfamiliar icon-only controls need tooltips.

### 5. Permission Flow
When Python requests approval:
- Show a modal sheet with tool name, risk reason, source session/task, and structured input.
- Actions: `拒绝`, `允许一次`, `本会话始终允许`, `信任当前工作区`。
- Keyboard: `Esc` deny, `Enter` allow once.
- The underlying turn remains visible and marked as waiting.

### 6. Runtime And Task Awareness
V0 must show:
- Current model/profile.
- Current permission mode.
- Current workspace path or compact label.
- Current git branch when available.
- Turn status: idle, thinking, executing tool, waiting permission, interrupted, finished.

Later:
- Subagent task queue.
- Voice state.
- Goal mode progress.
- Memory candidates/review.

## Visual Design Requirements
- Dominant surface: near-black canvas, not purple/blue gradients.
- Sidebar may use subtle macOS vibrancy-like transparency, but content must remain readable.
- Accent color follows existing `src/voice_code/theme.py`: restrained warm red plus gray hierarchy.
- Cards are only for repeated task rows, modals, or framed tool details. Do not put cards inside cards.
- Border radius max 8px unless native mac control requires otherwise.
- Use icons for folder, search, branch, local execution, mic, send, settings and close.
- Preserve dense developer-tool rhythm: compact rows, readable line length, minimal decoration.
- Text must not overlap in 320px, 768px, 1024px and 1440px layouts.

Suggested token mapping:

```css
:root {
  --bg-primary: #000000;
  --bg-surface: #0b0b0b;
  --bg-elevated: #111111;
  --border-primary: #1c1c1c;
  --text-primary: #ebe7de;
  --text-secondary: #a59f95;
  --text-dim: #6f6961;
  --accent-red: #df6a5c;
}
```

## Functional Requirements

### V0 Must Have
- Launch as a macOS app window.
- No login screen.
- Open one local workspace.
- Create a new task/session.
- List recent sessions grouped by project.
- Resume a session.
- Send a prompt to the existing Python agent.
- Stream text, reasoning, tool calls, tool results, errors and finish events.
- Show and resolve permission requests.
- Interrupt a running turn.
- Persist transcripts using the existing `~/.reasoning/transcripts` model.

### V0 May Stub
- Search across sessions.
- Mic button.
- Model selector if only one profile is configured.
- Attachment/context picker.
- Right-side tool inspector.
- Goal mode.
- Subagent dashboard.

### V0 Must Not Include
- Login, OAuth, billing, team switching or cloud sync.
- Remote project execution.
- Editing the public `voice_code` packaging surface.
- Replacing the Python agent loop.

## Code Style
UI components should be focused and composed from smaller parts.

Example target shape:

```tsx
export function Composer({
  context,
  value,
  isBusy,
  onChange,
  onSubmit,
  onInterrupt,
}: ComposerProps) {
  return (
    <form className="composer" onSubmit={onSubmit}>
      <ComposerContextBar context={context} />
      <PromptTextArea value={value} disabled={isBusy} onChange={onChange} />
      <ComposerActions isBusy={isBusy} onInterrupt={onInterrupt} />
    </form>
  );
}
```

Conventions:
- Separate bridge/data hooks from presentation components.
- Prefer native button/input semantics.
- Use CSS variables for colors and spacing.
- Keep component files under roughly 200 lines.
- Use real Chinese copy that fits the target UI, not placeholder filler.

## Testing Strategy
Frontend:
- Unit test reducers/state transitions for streamed events.
- Component test composer, sidebar, permission modal, and transcript rows.
- Accessibility test keyboard navigation and modal focus handling.
- Visual smoke test at 320, 768, 1024 and 1440 widths.

Backend integration:
- Use fake agent events to test bridge event mapping.
- Use temp transcript directories for session list/resume tests.
- Verify permission request waits until the UI resolves it.

Manual:
- Launch app.
- Start a new local task in this repo.
- Ask for a small file read-only task.
- Confirm tool calls stream and permission UI appears for write/destructive work.
- Interrupt a long-running prompt.
- Quit and relaunch, then resume the session.

## Boundaries
Always:
- Reuse existing `agent_loop`, permissions, transcript and session state wherever possible.
- Keep first version local-first.
- Keep the screenshot as architecture inspiration, not brand cloning.
- Make busy/waiting/error states visible.
- Preserve TUI and CLI behavior while adding desktop.

Ask first:
- Adding large frontend frameworks beyond React/Tauri/Vite.
- Changing transcript schema.
- Moving transcript storage out of `~/.reasoning`.
- Adding login or cloud sync.
- Changing packaging for the public repo.

Never:
- Hide permission requests.
- Auto-approve write/destructive tools because the UI is local.
- Store API keys or secrets in frontend localStorage.
- Send project paths, transcript content, or tool results to a remote UI service.
- Block CLI/TUI from working while the desktop app is introduced.

## Success Criteria
- A reviewer can read this spec and the technical spec and know what V0 includes.
- The first app screen is the usable workspace, not a landing page.
- The page structure matches the Codex-like architecture: sidebar, conversation canvas, composer, overlays.
- No login is required or visible.
- Existing Python tests still define the core behavior gates.
- Desktop implementation can be split into incremental PRs without redesigning the agent core.

## Open Questions
1. V0 app name in the window: `语码`, `Voice Code`, or both?
2. Should workspace opening default to the current repo, the last opened workspace, or a native folder picker?
3. Should the first mac app include voice controls immediately, or only reserve the mic button for later?
4. Should `/goal` mode appear as a normal task in V0, or wait until the base chat surface is stable?
5. Should the left sidebar group by physical project path only, or allow user-created folders like the screenshot?
