"""Entry point for ``python -m noxai`` — delegates to the CLI."""

from noxai.cli import app


def main() -> None:
    """Run the noxd daemon via the CLI ``run`` command."""
    app()


if __name__ == "__main__":
    main()
