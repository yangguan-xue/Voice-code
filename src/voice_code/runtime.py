"""Shared runtime bootstrap for CLI and interactive screen."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

import voice_code.llm.models as model_config
from voice_code.context import get_context
from voice_code.integrations import load_mcp_tools
from voice_code.llm.models import init_model, set_active_profile
from voice_code.memory.bootstrap import build_memory_rag_service
from voice_code.memory.paths import get_project_memory_key
from voice_code.memory.rag_service import MemoryRagService
from voice_code.prompt_cache import cached_prompt
from voice_code.prompts import get_system_prompt
from voice_code.security import configure_workspace_root
from voice_code.session import (
    TranscriptWriter,
    build_session_runtime_state,
    get_session_path,
    get_session_state_path,
    make_session_id,
    save_session_state,
)
from voice_code.skills import discover_skills, render_skills_prompt
from voice_code.tools import get_all_tools


@dataclass
class RuntimeBootstrap:
    model: ChatOpenAI
    fallback_model: ChatOpenAI | None
    fallback_profile: str | None
    tools: list
    cwd: str
    prompt: str
    session_id: str
    transcript_writer: TranscriptWriter
    resume_messages: list[BaseMessage] | None
    memory_service: MemoryRagService | None
    memory_project_key: str


async def build_workspace_prompt(
    *,
    workspace: str,
    tools: list,
    model_name: str,
    use_cache: bool = True,
) -> str:
    ctx = await get_context(workspace)
    skills_prompt = render_skills_prompt(discover_skills(workspace))
    def build() -> str:
        return get_system_prompt(
            tools=tools,
            cwd=workspace,
            model_name=model_name,
            project_instructions=ctx.get("projectInstructions", ""),
            git_status=ctx.get("gitStatus", ""),
            skills_prompt=skills_prompt,
        )
    if not use_cache:
        return build()
    return cached_prompt(
        workspace=workspace,
        inputs={
            "tools": [(tool.name, tool.description) for tool in tools],
            "cwd": workspace,
            "model_name": model_name,
            "project_instructions": ctx.get("projectInstructions", ""),
            "git_status": ctx.get("gitStatus", ""),
            "skills_prompt": skills_prompt,
        },
        builder=build,
    )


def resolve_fallback_profile(profile: str | None) -> str | None:
    if not profile:
        return None
    env_values = model_config._read_env_file(model_config._ENV_FILE)
    profile_values = model_config._resolve_profile(profile, env_values)
    fallback = profile_values.get("fallback_profile")
    return str(fallback) if fallback else None


async def bootstrap_runtime(
    *,
    profile: str | None,
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    active_profile_name: str | None = None,
    session_id: str | None = None,
    resume_messages: list[BaseMessage] | None = None,
    workspace: str | None = None,
) -> RuntimeBootstrap:
    model_kwargs: dict[str, str] = {}
    if profile:
        model_kwargs["profile"] = profile
    if model_name:
        model_kwargs["model_name"] = model_name
    if api_key:
        model_kwargs["api_key"] = api_key
    if base_url:
        model_kwargs["base_url"] = base_url

    active_profile = active_profile_name or model_config.resolve_profile_name(profile)
    model = init_model(**model_kwargs)
    set_active_profile(active_profile)
    fallback_profile = resolve_fallback_profile(profile)
    fallback_model = init_model(profile=fallback_profile) if fallback_profile else None

    cwd = str(Path(workspace or os.getcwd()).expanduser().resolve(strict=True))
    configure_workspace_root(cwd)
    tools = get_all_tools()
    tools.extend(await load_mcp_tools(cwd))
    prompt = await build_workspace_prompt(
        workspace=cwd,
        tools=tools,
        model_name=model.model_name,
    )
    memory_service = build_memory_rag_service()

    resolved_session_id = session_id or make_session_id()
    transcript_writer = TranscriptWriter(
        get_session_path(resolved_session_id),
        session_meta={"cwd": cwd},
    )
    state_path = get_session_state_path(resolved_session_id)
    if not state_path.exists():
        save_session_state(
            build_session_runtime_state(
                session_id=resolved_session_id,
                cwd=cwd,
                agent_mode="default",
                model_name=model.model_name,
            )
        )
    return RuntimeBootstrap(
        model=model,
        fallback_model=fallback_model,
        fallback_profile=fallback_profile,
        tools=tools,
        cwd=cwd,
        prompt=prompt,
        session_id=resolved_session_id,
        transcript_writer=transcript_writer,
        resume_messages=resume_messages,
        memory_service=memory_service,
        memory_project_key=get_project_memory_key(cwd),
    )
