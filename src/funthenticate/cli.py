from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .core import default_fun_auth_ideas, default_fun_prompts


def main(argv: Sequence[str] | None = None) -> None:
    normalized_argv = None
    json_output = False
    if argv is not None:
        json_output = "--json" in argv
        normalized_argv = [argument for argument in argv if argument != "--json"]

    parser = argparse.ArgumentParser(prog="funthenticate")
    parser.add_argument("--json", action="store_true", help="Render output as JSON.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("prompts", help="List built-in prompt keys.")
    subparsers.add_parser("ideas", help="List built-in idea keys.")
    subparsers.add_parser("demo-sequence", help="Show a small prompt sequence.")
    args = parser.parse_args(normalized_argv)
    json_output = json_output or args.json

    command = args.command or "summary"
    if command == "prompts":
        payload: object = [prompt.key for prompt in default_fun_prompts()]
    elif command == "ideas":
        payload = [idea.key for idea in default_fun_auth_ideas()]
    elif command == "demo-sequence":
        payload = {
            "prompt_keys": [
                "authorized-popup",
                "draw-key",
                "operator-conversion-lock",
            ],
            "finish_with": "complete_fun or redirect_to_provider",
        }
    else:
        payload = {
            "name": "funthenticate",
            "prompts": [prompt.key for prompt in default_fun_prompts()],
            "ideas": [idea.key for idea in default_fun_auth_ideas()],
        }

    if json_output:
        print(json.dumps(payload, indent=2))
        return
    _print_text(command, payload)


def _print_text(command: str, payload: object) -> None:
    if isinstance(payload, list):
        for item in payload:
            print(item)
        return
    if command == "demo-sequence" and isinstance(payload, dict):
        print(" -> ".join(str(item) for item in payload["prompt_keys"]))
        print(f"finish: {payload['finish_with']}")
        return
    if isinstance(payload, dict):
        print(payload["name"])
        print("prompts: " + ", ".join(str(item) for item in payload["prompts"]))
        print("ideas: " + ", ".join(str(item) for item in payload["ideas"]))
