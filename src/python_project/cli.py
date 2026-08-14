"""Command-line entry point for the project."""

from __future__ import annotations

import argparse


def greeting(name: str) -> str:
    """Return a friendly greeting for ``name``."""
    return f"Hello, {name}!"


def main() -> None:
    """Parse command-line arguments and print a greeting."""
    parser = argparse.ArgumentParser(description="Say hello from Python.")
    parser.add_argument(
        "--name",
        default="world",
        help="The name to greet (default: world).",
    )
    args = parser.parse_args()
    print(greeting(args.name))


if __name__ == "__main__":
    main()
