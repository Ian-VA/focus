import typer
from focus.agent.agent import DEFAULT_MODELS, create_agent
from focus.utils.log import warn, err, log

import os
from pyfiglet import figlet_format

app = typer.Typer()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    key: str | None = typer.Option(None, "--key", help="api key to use for Focus"),
    provider: str = typer.Option(
        "anthropic", "--provider", help="model provider to use for Focus (anthropic, openai)"
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
        20.0, "--interval", help="seconds to wait between screen check-ins"
    ),
    effort: str = typer.Option(
        "low", "--effort", help="how hard the model thinks per check-in (low, medium, high)"
    ),
):
    if ctx.invoked_subcommand is None:
        start_focus(provider, model, key, task, interval, effort)


def start_focus(
    provider: str,
    model: str | None,
    key: str | None = None,
    task: str | None = None,
    interval: float = 20.0,
    effort: str = "low",
):
    if key == None:
        warn("provider api key not given. searching...")
        key = os.getenv(provider.upper() + "_API_KEY")

        if key == None:
            err(
                "provider api key not found. please set $OPENAI_API_KEY, $ANTHROPIC_API_KEY, or pass in api_key with the [green]--key[/green] argument"
            )

    f = figlet_format("FOCUS!", font="slant")
    print(f)
    agent = create_agent(
        provider, model, api_key=key, task=task, interval=interval, effort=effort
    )
    log("focus is now running :)")
    agent.run()
