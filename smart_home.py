"""Smart home control via a local Home Assistant instance's REST API.

Home Assistant unifies most smart-home brands (Philips Hue, TP-Link Kasa, Nest, etc.)
behind one REST API: a base URL plus a long-lived access token, calling
POST /api/services/<domain>/<service> to act on a device and GET /api/states[/<entity_id>]
to read state. Expects `ha_url` and `ha_token` in config.json.
"""
import requests

# Domains a voice assistant would plausibly care about. Home Assistant instances can have
# hundreds of entities (automations, scripts, zones, update entities, ...) that aren't useful
# to read aloud, so list_devices() is scoped to these.
_RELEVANT_DOMAINS = ("light", "switch", "climate", "sensor")

# Only these sensor device classes (or units) are included — a typical HA setup has dozens of
# sensors (battery levels, signal strength, uptime, ...) that a voice assistant has no reason
# to mention when asked about "the house".
_SENSOR_DEVICE_CLASSES = ("temperature", "humidity")
_SENSOR_UNITS = ("°C", "°F", "%")

_DOMAIN_LABELS = {"light": "Lights", "switch": "Switches", "climate": "Climate", "sensor": "Sensors"}

# entity domain -> supported action -> Home Assistant service name
_SUPPORTED_ACTIONS = {
    "light": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "switch": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "climate": {"turn_on": "turn_on", "turn_off": "turn_off", "set_temperature": "set_temperature"},
}

LIST_DEVICES_SCHEMA = {
    "name": "list_devices",
    "description": (
        "List the smart home devices (lights, switches, thermostats, and temperature/humidity sensors) "
        "and their current states. Use this when the user asks what devices exist or wants a status overview."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

CONTROL_DEVICE_SCHEMA = {
    "name": "control_device",
    "description": (
        "Control a smart home device: turn a light or switch on/off (or toggle it), or set a thermostat's "
        "target temperature. Use this for commands like 'turn on the lights', 'turn off the living room switch', "
        "or 'set the thermostat to 70'. entity_id must be a Home Assistant entity id like 'light.living_room' "
        "or 'climate.hallway' — use list_devices or get_device_state first if you don't already know it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The Home Assistant entity id to control, e.g. 'light.living_room' or 'climate.hallway'.",
            },
            "action": {
                "type": "string",
                "enum": ["turn_on", "turn_off", "toggle", "set_temperature"],
                "description": "The action to perform.",
            },
            "value": {
                "type": "number",
                "description": "Target value for the action, e.g. the target temperature for 'set_temperature'. Not needed for turn_on/turn_off/toggle.",
            },
        },
        "required": ["entity_id", "action"],
    },
}

GET_DEVICE_STATE_SCHEMA = {
    "name": "get_device_state",
    "description": (
        "Get the current state of a single smart home device (e.g. current temperature, on/off status). "
        "Identify the device by its friendly name (e.g. 'living room thermostat') or its exact entity_id."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity_id_or_name": {
                "type": "string",
                "description": "The device's friendly name or exact Home Assistant entity id.",
            }
        },
        "required": ["entity_id_or_name"],
    },
}


def _resolve_config(config: dict) -> tuple[str | None, str | None, str | None]:
    """Returns (base_url, token, None) or (None, None, error_message)."""
    config = config or {}
    ha_url = config.get("ha_url")
    ha_token = config.get("ha_token")
    if not ha_url or not ha_token:
        return None, None, "Smart home isn't configured — add ha_url and ha_token to config.json"
    return ha_url.rstrip("/"), ha_token, None


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _raw_request(method: str, url: str, token: str, **kwargs):
    """Runs a request, never raises. Returns (response, None) or (None, error_message)."""
    try:
        return requests.request(method, url, headers=_headers(token), timeout=8, **kwargs), None
    except requests.exceptions.ConnectionError:
        return None, f"Couldn't reach Home Assistant at {url}. Is it running?"
    except requests.exceptions.Timeout:
        return None, "Home Assistant didn't respond in time."
    except requests.RequestException as e:
        return None, f"Smart home request failed: {e}"


def _status_error(resp) -> str | None:
    """Returns a user-facing error for a bad status code, or None if the response is OK."""
    if resp.status_code in (401, 403):
        return "Home Assistant rejected the access token — check ha_token in config.json."
    if not resp.ok:
        return f"Home Assistant returned an error ({resp.status_code})."
    return None


def _is_relevant_sensor(entity: dict) -> bool:
    attrs = entity.get("attributes", {})
    if attrs.get("device_class") in _SENSOR_DEVICE_CLASSES:
        return True
    return attrs.get("unit_of_measurement") in _SENSOR_UNITS


def _format_entity_line(entity: dict) -> str:
    entity_id = entity.get("entity_id", "unknown")
    name = entity.get("attributes", {}).get("friendly_name", entity_id)
    state = entity.get("state", "unknown")
    unit = entity.get("attributes", {}).get("unit_of_measurement")
    state_str = f"{state}{unit}" if unit else state
    return f"  - {name} ({entity_id}): {state_str}"


def _format_state(entity: dict) -> str:
    entity_id = entity.get("entity_id", "unknown")
    attrs = entity.get("attributes", {})
    name = attrs.get("friendly_name", entity_id)
    state = entity.get("state", "unknown")
    unit = attrs.get("unit_of_measurement")

    details = [f"{state}{unit}" if unit else state]
    if attrs.get("current_temperature") is not None:
        details.append(f"current temperature {attrs['current_temperature']}")
    if attrs.get("temperature") is not None:
        details.append(f"target temperature {attrs['temperature']}")
    if attrs.get("humidity") is not None:
        details.append(f"humidity {attrs['humidity']}%")

    return f"{name} ({entity_id}): " + ", ".join(details)


def list_devices(config: dict) -> str:
    ha_url, ha_token, err = _resolve_config(config)
    if err:
        return err

    resp, err = _raw_request("GET", f"{ha_url}/api/states", ha_token)
    if err:
        return err
    status_err = _status_error(resp)
    if status_err:
        return status_err

    try:
        states = resp.json()
    except ValueError:
        return "Home Assistant returned an unreadable response."

    by_domain: dict[str, list[dict]] = {}
    for entity in states:
        entity_id = entity.get("entity_id", "")
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if domain not in _RELEVANT_DOMAINS:
            continue
        if domain == "sensor" and not _is_relevant_sensor(entity):
            continue
        by_domain.setdefault(domain, []).append(entity)

    if not by_domain:
        return "No lights, switches, climate devices, or temperature/humidity sensors found."

    lines = []
    for domain in _RELEVANT_DOMAINS:
        entities = by_domain.get(domain)
        if not entities:
            continue
        lines.append(f"{_DOMAIN_LABELS[domain]}:")
        lines.extend(_format_entity_line(e) for e in entities)
    return "\n".join(lines)


def control_device(entity_id: str, action: str, value: float | int | str | None, config: dict) -> str:
    if not entity_id or "." not in entity_id:
        return f"'{entity_id}' doesn't look like a valid entity id (expected e.g. 'light.living_room')."
    domain = entity_id.split(".", 1)[0]

    domain_actions = _SUPPORTED_ACTIONS.get(domain)
    if not domain_actions or action not in domain_actions:
        return f"Unsupported domain/action: can't do '{action}' on a '{domain}' device."

    payload = {"entity_id": entity_id}
    if action == "set_temperature":
        if value is None:
            return "set_temperature needs a target value."
        try:
            payload["temperature"] = float(value)
        except (TypeError, ValueError):
            return f"'{value}' isn't a valid temperature."

    ha_url, ha_token, err = _resolve_config(config)
    if err:
        return err

    service = domain_actions[action]
    resp, err = _raw_request("POST", f"{ha_url}/api/services/{domain}/{service}", ha_token, json=payload)
    if err:
        return err
    status_err = _status_error(resp)
    if status_err:
        return status_err

    if action == "set_temperature":
        return f"Set {entity_id} to {payload['temperature']}."
    verb = {"turn_on": "Turned on", "turn_off": "Turned off", "toggle": "Toggled"}[action]
    return f"{verb} {entity_id}."


def get_device_state(entity_id_or_name: str, config: dict) -> str:
    ha_url, ha_token, err = _resolve_config(config)
    if err:
        return err

    query = (entity_id_or_name or "").strip()
    if not query:
        return "No device name or entity id given."

    looks_like_entity_id = "." in query and " " not in query
    if looks_like_entity_id:
        resp, err = _raw_request("GET", f"{ha_url}/api/states/{query}", ha_token)
        if err:
            return err
        if resp.status_code == 200:
            try:
                return _format_state(resp.json())
            except ValueError:
                return "Home Assistant returned an unreadable response."
        if resp.status_code != 404:
            status_err = _status_error(resp)
            if status_err:
                return status_err
        # 404 (or non-fatal status) falls through to a friendly-name search below.

    resp, err = _raw_request("GET", f"{ha_url}/api/states", ha_token)
    if err:
        return err
    status_err = _status_error(resp)
    if status_err:
        return status_err

    try:
        states = resp.json()
    except ValueError:
        return "Home Assistant returned an unreadable response."

    match = next(
        (s for s in states if query.lower() in s.get("attributes", {}).get("friendly_name", "").lower()),
        None,
    )
    if not match:
        return f"Couldn't find a device matching '{entity_id_or_name}'."
    return _format_state(match)
