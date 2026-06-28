"""Shared runtime bootstrap for CLI and interactive screen."""

from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

import reasoning_agent.llm.models as model_config
from reasoning_agent.context import get_context
from reasoning_agent.llm.models import init_model
from reasoning_agent.prompts import get_system_prompt
from reasoning_agent.session import TranscriptWriter, get_session_path, make_session_id
from reasoning_agent.tools import get_all_tools


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
    session_id: str | None = None,
    resume_messages: list[BaseMessage] | None = None,
) -> RuntimeBootstrap:
    model_kwargs: dict[str, str] = {}
    if profile:
        model_kwargs["profile"] = profile
    if model_name:
        model_kwargs["model_name"] = model_name

    model = init_model(**model_kwargs)
    fallback_profile = resolve_fallback_profile(profile)
    fallback_model = init_model(profile=fallback_profile) if fallback_profile else None

    tools = get_all_tools()
    cwd = os.getcwd()
    ctx = await get_context(cwd)
    prompt = get_system_prompt(
        tools=tools,
        cwd=cwd,
        model_name=model.model_name,
        claude_md=ctx.get("claudeMd", ""),
        git_status=ctx.get("gitStatus", ""),
    )

    resolved_session_id = session_id or make_session_id()
    transcript_writer = TranscriptWriter(get_session_path(resolved_session_id))
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
    )
