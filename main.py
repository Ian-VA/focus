import typer


app = typer.Typer()


@app.callback()
def callback():


@app.command()
def hello(name: str):
    print("Hello!")

@app.command()
def goodbye(name: str):
    print("Goodbye!")

if __name__ == "__main__":
    app()