import typer
from focus.agent.agent import AGENTS, CLOUD_PROVIDERS, DEFAULT_MODELS, LocalAgent, create_agent
from focus.tools.platform import PlatformError, get_platform
from focus.utils.log import warn, err, log

import os
from pyfiglet import figlet_format

app = typer.Typer()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    provider: str = typer.Option(
        "local",
        "--provider",
        help=f"model provider to use for Focus: local (default, any OpenAI-compatible server such as ollama), {', '.join(CLOUD_PROVIDERS)}",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "--api_key",
        help=f"api key for a cloud provider. only used with --provider {' or '.join(CLOUD_PROVIDERS)}",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help=f"url of the model server. defaults to {LocalAgent.DEFAULT_BASE_URL} for local models and the provider's own api otherwise",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=f"model type to use for Focus (defaults: {', '.join(f'{p}={m}' for p, m in DEFAULT_MODELS.items())})",
    ),
    task: str | None = typer.Option(
        None,
        "--task",
        help="task you wish to complete in this Focus session. without one, Focus keeps you on generally productive work",
    ),
    interval: float = typer.Option(
        5.0,
        "--interval",
        help="starting seconds between screen check-ins. backs off to 60s while you stay on task",
    ),
    effort: str = typer.Option(
        "low",
        "--effort",
        help="how hard the model thinks per check-in (low, medium, high). cloud providers only",
    ),
):
    if ctx.invoked_subcommand is None:
        start_focus(provider, model, api_key, base_url, task, interval, effort)


def start_focus(
    provider: str,
    model: str | None,
    api_key: str | None = None,
    base_url: str | None = None,
    task: str | None = None,
    interval: float = 5.0,
    effort: str = "low",
):
    if provider not in AGENTS:
        err(f"unknown provider '{provider}'. choose one of: {', '.join(AGENTS)}")

    if provider == "local":
        if api_key is not None:
            err(
                "an api key was given without a cloud provider. add [green]--provider[/green] "
                f"{' or '.join(CLOUD_PROVIDERS)} to use it, or drop the key to run a local model"
            )
    else:
        env_var = provider.upper() + "_API_KEY"
        if api_key is None:
            warn(f"api key not given with --api-key. checking ${env_var}...")
            api_key = os.getenv(env_var)
        if api_key is None:
            err(
                f"no api key for {provider}. pass it with [green]--api-key[/green] or set ${env_var}. "
                "or drop --provider to run a local model, which needs no key"
            )

    f = figlet_format("FOCUS!", font="slant")
    print(f)
    try:
        get_platform().check_permissions()
    except PlatformError as e:
        err(str(e))

    agent = create_agent(
        provider,
        model,
        api_key=api_key,
        base_url=base_url,
        task=task,
        interval=interval,
        effort=effort,
    )
    log("focus is now running :)")
    agent.run()
