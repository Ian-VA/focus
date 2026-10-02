import typer
from rich import print


app = typer.Typer()


@app.callback()
def callback():
    pass

@app.command()
def hello(name: str):
    print("Hello!")

@app.command()
def goodbye(name: str):
    print("Goodbye!")