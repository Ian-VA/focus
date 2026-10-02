import typer
from rich import print


def err(message: str):
    print(f"[bold red]error[/bold red]: {message}")
    raise typer.Exit()


def warn(message: str):
    print(f"[yellow]warning[/yellow]: {message}")


def log(message: str):
    print(f"[green]log[/green]: {message}")
