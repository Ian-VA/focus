import json
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import anthropic
import openai

from focus.agent.pacing import AdaptiveInterval, ScreenWatcher
from focus.tools import TOOLS, Screenshot, ToolResult, run_tool
from focus.utils.log import err, log, warn
from focus.utils.prompts import build_system_prompt, check_in


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    error: str | None = None


@dataclass
class Turn:
    """one model response, normalized across providers"""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stopped_early: str | None = None


class BaseAgent(ABC):
    DEFAULT_MODEL: str
    FATAL_ERRORS: tuple[type[Exception], ...] = ()

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        task: str | None = None,
        interval: float = 5.0,
        max_interval: float = 60.0,
        effort: str | None = "low",
        max_turns: int = 10,
        max_notes: int = 12,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.system_prompt = build_system_prompt(task)
        self.interval = AdaptiveInterval(interval, max_interval)
        self.watcher = ScreenWatcher(force_after=max_interval)
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
        return self._converse(text, images or [])[0]

    def _converse(self, text: str, images: list[Screenshot]) -> tuple[str, bool]:
        """returns the model's final text and whether it used any tools along the way"""
        conversation = self._user_message(text, images)
        used_tools = False

        for _ in range(self.max_turns):
            turn = self._complete(conversation)
            if turn.stopped_early:
                warn(turn.stopped_early)
            if turn.stopped_early or not turn.tool_calls:
                return turn.text, used_tools

            used_tools = True
            results = [(call, self.use_tool(call)) for call in turn.tool_calls]
            conversation.extend(self._tool_results(results))

        warn(f"check-in hit the {self.max_turns} turn limit")
        return turn.text, used_tools

    def check_in(self) -> str | None:
        """look at the screen and ask the model about it if it changed. None when skipped"""
        screen = self.capture_screen()
        if not screen.is_error and not self.watcher.needs_check(screen.images):
            self.interval.calm()
            return None

        notes = "\n".join(f"- {note}" for note in self.notes)
        text = check_in.format(
            time=time.strftime("%H:%M:%S"),
            elapsed=_format_duration(time.time() - self.started_at),
            notes=notes or "(none yet, this is the first check-in)",
        )
        if screen.is_error:
            text += f"\n\n(automatic screenshot failed: {screen.text})"

        summary, used_tools = self._converse(text, screen.images)
        if used_tools:
            self.interval.alert()
            self.watcher.reset()
        else:
            self.interval.calm()

        summary = summary.strip()
        if summary:
            self.notes.append(f"{time.strftime('%H:%M')} {summary.splitlines()[-1]}")
            log(summary)
        return summary

    def run(self) -> None:
        log(f"monitoring every {self.interval.base:g}s with {self.model}. ctrl+c to stop")
        try:
            while True:
                previous = self.interval.current
                try:
                    self.check_in()
                except self.FATAL_ERRORS as e:
                    err(f"{type(e).__name__}: {e}")
                except Exception as e:
                    detail = f"{type(e).__name__}: {e}"
                    if e.__cause__ is not None:
                        detail += f" (caused by {type(e.__cause__).__name__}: {e.__cause__})"
                    warn(f"check-in failed, retrying next interval: {detail}")
                if self.interval.current != previous:
                    log(f"checking every {self.interval.current:g}s")
                time.sleep(self.interval.current)
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
    FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
    MAX_TOKENS = 16000

    def __init__(self, model: str = DEFAULT_MODEL, **kwargs):
        super().__init__(model=model, **kwargs)
        self.client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url)
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
        conversation_cache = {"cache_control": {"type": "ephemeral"}} if len(conversation) > 1 else {}
        response = self.create_message(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            system=[
                {"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}
            ],
            tools=self.tools,
            messages=conversation,
            **conversation_cache,
            **self.request_options,
        )
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
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
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


class LocalAgent(BaseAgent):
    """a model served locally over the OpenAI chat completions protocol: ollama, lm studio, llama.cpp, vllm, ..."""

    DEFAULT_MODEL = "qwen3-vl"
    DEFAULT_BASE_URL = "http://localhost:11434/v1"
    FATAL_ERRORS = (openai.NotFoundError, openai.BadRequestError)
    MAX_LISTED_MODELS = 8

    def __init__(self, model: str = DEFAULT_MODEL, **kwargs):
        super().__init__(model=model, **kwargs)
        self.base_url = self.base_url or self.DEFAULT_BASE_URL
        self.client = openai.OpenAI(api_key=self.api_key or "local", base_url=self.base_url)
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in TOOLS
        ]
        self._check_server()

    def _check_server(self) -> None:
        try:
            available = {m.id for m in self.client.models.list()}
        except openai.APIConnectionError:
            err(
                f"no model server answered at {self.base_url}. start one (e.g. `ollama serve`), "
                "point [green]--base-url[/green] at yours, or use a cloud model with "
                "[green]--provider[/green] and [green]--api-key[/green]"
            )
        except openai.APIError:
            return

        if available and not {self.model, f"{self.model}:latest"} & available:
            listed = ", ".join(sorted(available)[: self.MAX_LISTED_MODELS])
            err(
                f"model '{self.model}' isn't available at {self.base_url}. pull it first "
                f"(e.g. `ollama pull {self.model}`) or pick one with [green]--model[/green]: {listed}"
            )

    def _user_message(self, text, images):
        content = [self._image(shot) for shot in images]
        content.append({"type": "text", "text": text})
        return [{"role": "user", "content": content}]

    def _complete(self, conversation):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system_prompt}, *conversation],
            tools=self.tools,
        )
        choice = response.choices[0]
        message = choice.message

        item: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in message.tool_calls
            ]
        conversation.append(item)

        return Turn(
            text=message.content or "",
            tool_calls=[self._parse_call(call) for call in message.tool_calls or []],
            stopped_early={
                "length": "the model ran out of output tokens mid-check-in",
                "content_filter": "the model declined to review this screen",
            }.get(choice.finish_reason),
        )

    def _tool_results(self, results):
        items: list[dict[str, Any]] = []
        images: list[Screenshot] = []
        for call, result in results:
            text = f"error: {result.text}" if result.is_error else result.text
            items.append({"role": "tool", "tool_call_id": call.id, "content": text})
            images.extend(result.images)

        if images:
            items.append(
                {
                    "role": "user",
                    "content": [
                        *map(self._image, images),
                        {"type": "text", "text": "(the screenshots from the tool results above)"},
                    ],
                }
            )
        return items

    @staticmethod
    def _parse_call(call) -> ToolCall:
        arguments = call.function.arguments
        if isinstance(arguments, dict):
            return ToolCall(call.id, call.function.name, arguments)
        try:
            return ToolCall(call.id, call.function.name, json.loads(arguments or "{}"))
        except json.JSONDecodeError:
            return ToolCall(call.id, call.function.name, {}, error="tool arguments were not valid JSON")

    @staticmethod
    def _image(shot: Screenshot) -> dict[str, Any]:
        return {"type": "image_url", "image_url": {"url": shot.data_url}}


AGENTS: dict[str, type[BaseAgent]] = {
    "local": LocalAgent,
    "anthropic": AnthropicAgent,
    "openai": OpenAIAgent,
}
CLOUD_PROVIDERS = tuple(provider for provider in AGENTS if provider != "local")
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
