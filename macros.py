"""Multi-step workflow/macro support: run a configured sequence of steps as one voice command."""
import app_launcher

RUN_MACRO_SCHEMA = {
    "name": "run_macro",
    "description": (
        "Run a configured multi-step macro/workflow by name (e.g. 'start my work session'). "
        "Executes each configured step in order (like launching a set of apps) and reports what "
        "happened. Match by closest name, doesn't need to be exact."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The macro name to run, e.g. 'work session'.",
            }
        },
        "required": ["name"],
    },
}

LIST_MACROS_SCHEMA = {
    "name": "list_macros",
    "description": (
        "List the user's configured macros/workflows and what each one does. Use this when the "
        "user asks what macros/workflows are available, or what a specific macro does."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


def _find_macro(name: str, macros: dict) -> tuple[str, list] | None:
    lname = name.lower()
    for key, steps in macros.items():
        if key.lower() == lname:
            return key, steps

    match_key = next((key for key in macros if lname in key.lower() or key.lower() in lname), None)
    if match_key is not None:
        return match_key, macros[match_key]
    return None


def _describe_step(step: dict) -> str:
    action = step.get("action", "?")
    target = step.get("target", "?")
    return f"{action}:{target}"


def _run_step(step: dict, config: dict) -> str:
    action = step.get("action")

    if action == "launch_app":
        target = step.get("target", "")
        return app_launcher.launch_app(target, config.get("apps", {}))

    return f"Unsupported macro step type: {action}"


def run_macro(name: str, config: dict) -> str:
    macros = config.get("macros") or {}
    if not macros:
        return "No macros configured. Add a 'macros' section to config.json."

    found = _find_macro(name, macros)
    if found is None:
        return f"Couldn't find a macro matching '{name}'."
    macro_name, steps = found

    if not steps:
        return f"Macro '{macro_name}' has no steps configured."

    successes = []
    failures = []
    for step in steps:
        try:
            result = _run_step(step, config)
        except Exception as e:
            result = f"error running step ({_describe_step(step)}): {e}"

        label = _describe_step(step)
        is_failure = result.lower().startswith(("couldn't", "unsupported", "error")) or "error" in result.lower()
        if is_failure:
            failures.append(f"{label} failed because {result}")
        else:
            successes.append(result)

    if not failures:
        return f"Ran macro '{macro_name}': " + "; ".join(successes)
    if not successes:
        return f"Macro '{macro_name}' failed entirely: " + "; ".join(failures)

    return (
        f"Ran macro '{macro_name}' with some issues. Did: " + "; ".join(successes)
        + ". But failed: " + "; ".join(failures)
    )


def list_macros(config: dict) -> str:
    macros = config.get("macros") or {}
    if not macros:
        return "No macros configured. Add a 'macros' section to config.json."

    lines = []
    for name, steps in macros.items():
        step_count = len(steps)
        step_summary = ", ".join(_describe_step(s) for s in steps) if steps else "no steps"
        lines.append(f"- {name} ({step_count} step{'s' if step_count != 1 else ''}): {step_summary}")
    return "\n".join(lines)
