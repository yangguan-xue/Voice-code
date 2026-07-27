# Voice Code · 语码

语码是一个本地优先的编程 agent，包含 CLI、TUI、语音模式、桌面端和 Web Demo。

- 在线 Web Demo：https://voice-code.yangguanxue.top/
- 公开仓库：https://github.com/yangguan-xue/Voice-code
- 许可证：AGPL-3.0
- Python：3.12+

## 可以展示什么

| 形态 | 状态 | 说明 |
| --- | --- | --- |
| CLI / TUI | 可本地开发体验 | `uv run reasoning` |
| 语音模式 | 实验性 | 唤醒词、STT、Agent、TTS |
| 桌面端 | 可展示打包路径 | Tauri shell 位于 `desktop/` |
| Web Demo | 已部署预览 | 受控沙盒，访问码进入 |

## 快速开始

```bash
uv sync
cp .env.example .env
uv run reasoning
```

纯命令行：

```bash
uv run reasoning --plain
```

语音模式：

```bash
uv run reasoning-voice
```

## 配置

在 `.env` 或 `models.toml` 中配置模型：

```ini
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_NAME=deepseek-v4-pro
```

`models.toml` 支持多个 OpenAI-compatible profile。

## 桌面端

```bash
cd desktop
pnpm install
pnpm test
pnpm build
```

Windows runtime 安装包：

```powershell
pnpm tauri:build:windows:runtime
```

runtime 安装包会在打包阶段内置 app-owned Python runtime，用户不需要为了启动桌面端额外安装
Python 或 `uv`。真实调用模型仍需要配置模型/API key；涉及 Git 的项目能力仍需要系统 Git。

## Web Demo

前端：

```bash
cd web
pnpm install
pnpm test -- --run
pnpm build
```

后端：

```bash
uv run reasoning-web-demo --host 127.0.0.1 --port 8787
```

部署模板在 `deploy/web-demo/`。

## 目录

```text
src/voice_code/      Python agent runtime
desktop/             Tauri 桌面壳
web/                 React Web Demo 前端
deploy/web-demo/     Nginx/systemd/Docker 部署模板
tools/               发布打包辅助脚本
tests/               Python 测试
docs/                公开架构和发布说明
```

## 安全提示

语码可以执行 shell 命令和编辑文件。日常使用建议保持权限确认开启，执行前审查工具请求，不要在
敏感目录中运行不可信模型配置或提示词。

公开 Web Demo 是受控沙盒，用于项目预览，不是通用云端开发环境。

## 公开仓库策略

这个仓库是公开发布面，不包含私有参考代码、本地 workspace、生成的 runtime bundle、虚拟环境、
构建产物或凭据。

## License

AGPL-3.0，见 [LICENSE](LICENSE)。
