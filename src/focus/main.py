import typer
from focus.agent.agent import BaseAgent
from focus.utils.log import warn, err, log
from focus.utils.prompts import default_task

import os
from pyfiglet import figlet_format

app = typer.Typer()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    key: str | None = typer.Option(None, "--key", help="api key to use for Focus"),
    provider: str = typer.Option(
        "anthropic", "--provider", help="model provider to use for Focus"
    ),
    model: str = typer.Option(
        "claude-sonnet-5-5", "--model", help="model type to use for Focus"
    ),
    task: str = typer.Option(
        default_task, "--task", help="task you wish to complete in this Focus session"
    ),
):
    if ctx.invoked_subcommand is None:
        start_focus(provider, model, key)


@app.command()
def start_focus(provider: str, model: str, key: str | None = None):
    if key == None:
        warn("provider api key not given. searching...")
        key = os.getenv(provider.upper() + "_API_KEY")

        if key == None:
            err(
                "provider api key not found. please set $OPENAI_API_KEY, $ANTHROPIC_API_KEY, or pass in api_key with the [green]--key[/green] argument"
            )

    f = figlet_format("FOCUS!", font="slant")
    print(f)
    BaseAgent(model=model, api_key=key, provider=provider)
    log("focus is now running :)")
