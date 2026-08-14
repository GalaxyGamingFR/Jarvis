"""Launches local Windows applications by name or opens arbitrary paths/URIs."""
import os
import subprocess

TOOL_SCHEMA = {
    "name": "launch_app",
    "description": (
        "Launch an application on the user's Windows machine, or open a file path / URL / URI. "
        "Use this when the user asks to open, launch, or start something (e.g. 'open Spotify', "
        "'launch VS Code', 'open my Obsidian inbox')."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": (
                    "The app key from the configured app registry (e.g. 'spotify', 'vscode', "
                    "'chrome', 'obsidian', 'notepad', 'explorer'), OR any file path / URL / URI to open directly."
                ),
            }
        },
        "required": ["app"],
    },
}


def launch_app(app: str, apps_config: dict) -> str:
    target = apps_config.get(app.lower(), app)

    try:
        if target.startswith(("http://", "https://", "spotify:", "obsidian:")):
            os.startfile(target)
        elif target in ("explorer",):
            subprocess.Popen(["explorer"])
        else:
            subprocess.Popen(target, shell=True)
        return f"Launched '{app}'."
    except FileNotFoundError:
        try:
            os.startfile(target)
            return f"Launched '{app}'."
        except OSError as e:
            return f"Couldn't launch '{app}': {e}"
    except OSError as e:
        return f"Couldn't launch '{app}': {e}"
