import json
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import anthropic
import openai

from focus.tools import TOOLS, Screenshot, ToolResult, run_tool
from focus.utils.log import err, log, warn
from focus.utils.prompts import build_system_prompt, check_in


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    # set when the model's arguments couldn't be parsed; the call is reported back, not run
    error: str | None = None


@dataclass
class Turn:
    """one model response, normalized across providers"""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stopped_early: str | None = None


class BaseAgent(ABC):
    DEFAULT_MODEL: str
    # API errors that retrying won't fix: bad key, unknown model, malformed request
    FATAL_ERRORS: tuple[type[Exception], ...] = ()

    def __init__(
        self,
        model: str,
        api_key: str,
        task: str | None = None,
        interval: float = 20.0,
        effort: str | None = "low",
        max_turns: int = 10,
        max_notes: int = 12,
    ):
        self.model = model
        self.api_key = api_key
        self.system_prompt = build_system_prompt(task)
        self.interval = interval
        self.effort = effort
        self.max_turns = max_turns
        self.notes: deque[str] = deque(maxlen=max_notes)
        self.started_at = time.time()

    @abstractmethod
    def _user_message(self, text: str, images: list[Screenshot]) -> list[Any]:
        """conversation items for a user message"""

    @abstractmethod
    def _complete(self, conversation: list[Any]) -> Turn:
        """call the model, append its output to the conversation, and parse the turn"""

    @abstractmethod
    def _tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> list[Any]:
        """conversation items reporting tool results back to the model"""

    def capture_screen(self) -> ToolResult:
        return run_tool("capture_screen", {})

    def use_tool(self, call: ToolCall) -> ToolResult:
        if call.error:
            result = ToolResult(call.error, is_error=True)
        else:
            result = run_tool(call.name, call.args)

        args = ", ".join(f"{k}={v!r}" for k, v in call.args.items())
        line = f"[cyan]{call.name}[/cyan]({args})"
        if result.is_error:
            warn(f"{line} -> {result.text}")
        else:
            log(line)
        return result

    def prompt(self, text: str, images: list[Screenshot] | None = None) -> str:
        """send the model a prompt and let it use tools until it's done. returns its final text"""
        conversation = self._user_message(text, images or [])

        for _ in range(self.max_turns):
            turn = self._complete(conversation)
            if turn.stopped_early:
                warn(turn.stopped_early)
            if turn.stopped_early or not turn.tool_calls:
                return turn.text

            results = [(call, self.use_tool(call)) for call in turn.tool_calls]
            conversation.extend(self._tool_results(results))

        warn(f"check-in hit the {self.max_turns} turn limit")
        return turn.text

    def check_in(self) -> str:
        notes = "\n".join(f"- {note}" for note in self.notes)
        text = check_in.format(
            time=time.strftime("%H:%M:%S"),
            elapsed=_format_duration(time.time() - self.started_at),
            notes=notes or "(none yet, this is the first check-in)",
        )
        screen = self.capture_screen()
        if screen.is_error:
            text += f"\n\n(automatic screenshot failed: {screen.text})"

        summary = self.prompt(text, screen.images).strip()
        if summary:
            # the model's closing line is its memory for the next check-in
            self.notes.append(f"{time.strftime('%H:%M')} {summary.splitlines()[-1]}")
            log(summary)
        return summary

    def run(self) -> None:
        log(f"monitoring every {self.interval:g}s with {self.model}. ctrl+c to stop")
        try:
            while True:
                try:
                    self.check_in()
                except self.FATAL_ERRORS as e:
                    err(f"{type(e).__name__}: {e}")
                except Exception as e:
                    warn(f"check-in failed, retrying next interval: {type(e).__name__}: {e}")
                time.sleep(self.interval)
        except KeyboardInterrupt:
            log("focus session ended. nice work :)")


class AnthropicAgent(BaseAgent):
    DEFAULT_MODEL = "claude-sonnet-5-5"
    FATAL_ERRORS = (
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
        anthropic.NotFoundError,
        anthropic.BadRequestError,
    )
    # models that accept the server-side refusal fallback. screen contents are
    # arbitrary, so a safety decline on one frame shouldn't end the check-in
    FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
    MAX_TOKENS = 16000

    def __init__(self, model: str = DEFAULT_MODEL, **kwargs):
        super().__init__(model=model, **kwargs)
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in TOOLS
        ]

        self.request_options: dict[str, Any] = {}
        if self.effort and not model.startswith("claude-haiku"):
            self.request_options["output_config"] = {"effort": self.effort}

        self.create_message = self.client.messages.create
        if model in self.FALLBACK_MODELS:
            self.create_message = self.client.beta.messages.create
            self.request_options |= {
                "betas": ["server-side-fallback-2026-07-01"],
                "fallbacks": "default",
            }

    def _user_message(self, text, images):
        content = [self._image(shot) for shot in images]
        content.append({"type": "text", "text": text})
        return [{"role": "user", "content": content}]

    def _complete(self, conversation):
        response = self.create_message(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            # system and tools never change, so this prefix stays cached across check-ins;
            # the top-level cache_control caches the growing conversation within one
            system=[
                {"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}
            ],
            cache_control={"type": "ephemeral"},
            tools=self.tools,
            messages=conversation,
            **self.request_options,
        )
        # append the content unchanged; thinking and fallback blocks must be sent back as-is
        conversation.append({"role": "assistant", "content": response.content})

        return Turn(
            text="\n".join(b.text for b in response.content if b.type == "text"),
            tool_calls=[
                ToolCall(b.id, b.name, dict(b.input))
                for b in response.content
                if b.type == "tool_use"
            ],
            stopped_early={
                "refusal": "the model declined to review this screen",
                "max_tokens": "the model ran out of output tokens mid-check-in",
            }.get(response.stop_reason),
        )

    def _tool_results(self, results):
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": [
                    {"type": "text", "text": result.text},
                    *(self._image(shot) for shot in result.images),
                ],
                "is_error": result.is_error,
            }
            for call, result in results
        ]
        # every result for a turn goes back in one user message
        return [{"role": "user", "content": blocks}]

    @staticmethod
    def _image(shot: Screenshot) -> dict[str, Any]:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": shot.media_type, "data": shot.b64},
        }


class OpenAIAgent(BaseAgent):
    DEFAULT_MODEL = "gpt-5"
    FATAL_ERRORS = (
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.NotFoundError,
        openai.BadRequestError,
    )

    def __init__(self, model: str = DEFAULT_MODEL, **kwargs):
        super().__init__(model=model, **kwargs)
        self.client = openai.OpenAI(api_key=self.api_key)
        self.tools = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "strict": False,
            }
            for t in TOOLS
        ]

        self.request_options: dict[str, Any] = {}
        if not model.startswith(("gpt-4", "gpt-3")):
            # with store=False, reasoning carries over between turns as encrypted items
            self.request_options["include"] = ["reasoning.encrypted_content"]
            if self.effort:
                self.request_options["reasoning"] = {"effort": self.effort}

    def _user_message(self, text, images):
        content = [self._image(shot) for shot in images]
        content.append({"type": "input_text", "text": text})
        return [{"role": "user", "content": content}]

    def _complete(self, conversation):
        response = self.client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=conversation,
            tools=self.tools,
            # screenshots are private, so nothing is kept server side
            store=False,
            **self.request_options,
        )
        conversation.extend(response.output)

        stopped_early = None
        if response.status == "incomplete":
            reason = getattr(response.incomplete_details, "reason", "unknown")
            stopped_early = f"the model stopped early ({reason})"

        return Turn(
            text=response.output_text,
            tool_calls=[
                self._parse_call(item) for item in response.output if item.type == "function_call"
            ],
            stopped_early=stopped_early,
        )

    def _tool_results(self, results):
        return [
            {"type": "function_call_output", "call_id": call.id, "output": self._output(result)}
            for call, result in results
        ]

    @staticmethod
    def _parse_call(item) -> ToolCall:
        try:
            return ToolCall(item.call_id, item.name, json.loads(item.arguments or "{}"))
        except json.JSONDecodeError:
            return ToolCall(item.call_id, item.name, {}, error="tool arguments were not valid JSON")

    @classmethod
    def _output(cls, result: ToolResult) -> str | list[dict[str, Any]]:
        text = f"error: {result.text}" if result.is_error else result.text
        if not result.images:
            return text
        return [{"type": "input_text", "text": text}, *map(cls._image, result.images)]

    @staticmethod
    def _image(shot: Screenshot) -> dict[str, Any]:
        return {"type": "input_image", "image_url": shot.data_url, "detail": "high"}


AGENTS: dict[str, type[BaseAgent]] = {
    "anthropic": AnthropicAgent,
    "openai": OpenAIAgent,
}
DEFAULT_MODELS = {provider: agent.DEFAULT_MODEL for provider, agent in AGENTS.items()}


def create_agent(provider: str, model: str | None = None, **kwargs) -> BaseAgent:
    if provider not in AGENTS:
        err(f"unknown provider '{provider}'. choose one of: {', '.join(AGENTS)}")
    agent = AGENTS[provider]
    return agent(model=model or agent.DEFAULT_MODEL, **kwargs)


def _format_duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"
