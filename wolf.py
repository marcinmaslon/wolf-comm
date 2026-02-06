import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

root = Path(__file__).resolve().parents[1] / "wolf-comm"
print(root)
sys.path.append(str(root))

import wolf_comm

from wolf_comm.constants import STATE, VALUE_ID
from wolf_comm.models import Device

def summarize_parameters(parameters: list):
    """Return a serializable summary of parameters for easier debugging."""
    return [
        {
            "name": parameter.name,
            "parameter_id": parameter.parameter_id,
            "value_id": parameter.value_id,
            "bundle_id": parameter.bundle_id,
            "read_only": parameter.read_only,
            "parent": parameter.parent,
        }
        for parameter in parameters
    ]


def summarize_values(values: list):
    """Return a serializable summary of parameter values for easier debugging."""
    return [
        {"value_id": value.value_id, "value": value.value, "state": value.state}
        for value in values
    ]


def log_pretty(name: str, entries: list, serializer):
    """Log a JSON dump of entries with a title and count."""
    payload = serializer(entries)
    logging.debug("%s (%d entries):\n%s", name, len(entries), json.dumps(payload, indent=2))


def _load_credentials():
    creds_path = Path(__file__).resolve().parent / "credentials.json"
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Missing credentials file at {creds_path}. "
            "Create one using credentials.example.json as a template."
        )

    with creds_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        raise ValueError(
            "credentials.json must include non-empty 'username' and 'password' keys."
        )

    mqtt_config = data.get("mqtt") or {}
    if not isinstance(mqtt_config, dict):
        mqtt_config = {}

    return {
        "username": username,
        "password": password,
        "mqtt": {
            "url": mqtt_config.get("url"),
            "username": mqtt_config.get("username"),
            "password": mqtt_config.get("password"),
        },
    }

_SYSTEM_CONTEXT_CACHE = Path.cwd() / "system_context_cache.json"


def _load_cached_system_context():
    if not _SYSTEM_CONTEXT_CACHE.exists():
        return None
    try:
        raw = _SYSTEM_CONTEXT_CACHE.read_text(encoding="utf-8")
        cache = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    expires_at = cache.get("expires_at")
    if not expires_at:
        return None
    try:
        expires_at_dt = datetime.fromisoformat(expires_at)
    except ValueError:
        return None

    if datetime.now() >= expires_at_dt:
        return None

    return cache


def _write_system_context_cache(system_list, parameters):
    data = {
        "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
        "systems": [
            {"id": system.id, "gateway": system.gateway, "name": system.name}
            for system in system_list
        ],
        "parameters": [
            {
                "name": parameter.name,
                "parameter_id": parameter.parameter_id,
                "value_id": parameter.value_id,
                "bundle_id": parameter.bundle_id,
                "read_only": parameter.read_only,
                "parent": parameter.parent,
            }
            for parameter in parameters
        ],
    }
    _SYSTEM_CONTEXT_CACHE.write_text(json.dumps(data), encoding="utf-8")


def _build_cached_parameters(entries):
    return [
        SimpleNamespace(
            name=entry["name"],
            parameter_id=entry["parameter_id"],
            value_id=entry["value_id"],
            bundle_id=entry["bundle_id"],
            read_only=entry["read_only"],
            parent=entry["parent"],
        )
        for entry in entries
    ]


def _build_client():
    credentials = _load_credentials()
    print("Connecting to Wolf")
    client = wolf_comm.WolfClient(credentials["username"], credentials["password"], region="de")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return client, loop, credentials


def _fetch_system_context(client, loop):
    cached = _load_cached_system_context()
    if cached:
        print("Using cached system context")
        systems = [
            Device(item["id"], item["gateway"], item["name"]) for item in cached["systems"]
        ]
        params = _build_cached_parameters(cached["parameters"])
        log_pretty("Parameter list (cached)", params, summarize_parameters)
        return systems, params

    print("Fetching devices")
    sl = loop.run_until_complete(client.fetch_system_list())
    print("Fetching parameter list")
    pl = loop.run_until_complete(client.fetch_parameters(sl[0].gateway, sl[0].id))
    log_pretty("Parameter list", pl, summarize_parameters)
    _write_system_context_cache(sl, pl)
    return sl, pl


def _set_parameter(client, loop, gateway_id, system_id, parameters, name, value):
    print(f"Setting {name} to {value}")
    target = next((param for param in parameters if param.name == name), None)
    if target is None:
        logging.warning("Parameter %s not found, skipping write", name)
        return

    payload = {VALUE_ID: target.value_id, STATE: str(value)}
    coroutine = client.write_value(gateway_id, system_id, target.bundle_id, payload)
    if loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            result = future.result()
        except Exception:
            logging.exception("MQTT-triggered write failed")
            return
    else:
        result = loop.run_until_complete(coroutine)
    logging.debug("Write response: %s", result)


def _parse_set_payload(payload: str):
    payload = payload.strip()
    if not payload:
        raise ValueError("empty payload")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        parts = payload.split(None, 1)
        if len(parts) != 2:
            raise ValueError("expected '<parameter> <value>' payload")
        return parts[0], parts[1]

    name = data.get("name") or data.get("parameter") or data.get("parameter_name")
    value = data.get("value")
    if name is None or value is None:
        raise ValueError("JSON payload must contain 'name' and 'value'")
    return name, value


def _create_mqtt_set_handler(client, loop, gateway_id, system_id, parameters):
    def _on_message(_mqtt_client, _userdata, msg):
        payload = msg.payload.decode("utf-8", errors="ignore")
        try:
            name, value = _parse_set_payload(payload)
        except ValueError as exc:
            logging.warning("Ignoring MQTT wolf/set payload: %s (%s)", exc, payload)
            return
        logging.info('MQTT wolf/set request for "%s" -> %s', name, value)
        _set_parameter(client, loop, gateway_id, system_id, parameters, name, value)

    return _on_message


def _parse_mqtt_url(value: str):
    parsed = urlparse(value)
    if not parsed.hostname:
        parsed = urlparse(f"//{value}", scheme="mqtt")
    host = parsed.hostname
    if not host:
        raise ValueError("Invalid MQTT URL; unable to determine host")
    scheme = (parsed.scheme or "mqtt").lower()
    port = parsed.port
    if port is None:
        port = 8883 if scheme in ("mqtts", "ssl") else 1883
    return host, port, scheme


def _resolve_mqtt_settings(mqtt_section):
    if not mqtt_section:
        return None
    url = mqtt_section.get("url")
    if not url:
        return None
    host, port, scheme = _parse_mqtt_url(url)
    username = mqtt_section.get("username")
    if isinstance(username, str):
        username = username.strip()
        if username.lower() == "anonymous" or username == "":
            username = None
    else:
        username = None
    password = mqtt_section.get("password")
    if isinstance(password, str) and password == "":
        password = None
    use_tls = scheme in ("mqtts", "ssl")
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": use_tls,
    }


def _configure_mqtt_client(mqtt_settings):
    if mqtt is None:
        raise RuntimeError("paho-mqtt is required for MQTT interactions.")
    client = mqtt.Client()
    if mqtt_settings["username"]:
        client.username_pw_set(
            mqtt_settings["username"], mqtt_settings["password"]
        )
    if mqtt_settings["use_tls"]:
        client.tls_set()
    client._wolf_mqtt_settings = mqtt_settings
    client._wolf_connected = False
    client._wolf_persistent = False
    return client


def _ensure_mqtt_connected(mqtt_client):
    if mqtt_client._wolf_connected:
        return
    settings = getattr(mqtt_client, "_wolf_mqtt_settings", None)
    if not settings:
        raise RuntimeError("MQTT client is missing configuration.")
    mqtt_client.connect(settings["host"], settings["port"])
    mqtt_client._wolf_connected = True


def _start_mqtt_set_listener(
    client, loop, gateway_id, system_id, parameters, mqtt_client
):
    if mqtt is None:
        raise RuntimeError(
            "paho-mqtt is required for MQTT listening; install it with `pip install paho-mqtt`."
        )

    mqtt_client._wolf_persistent = True

    def _on_connect(mqtt_client_obj, _userdata, _flags, rc):
        if rc != 0:
            logging.warning(
                "Failed to connect to MQTT broker (%s:%s), rc=%s",
                mqtt_client._wolf_mqtt_settings["host"],
                mqtt_client._wolf_mqtt_settings["port"],
                rc,
            )
            return
        mqtt_client_obj.subscribe("wolf/set")
        logging.info(
            "Subscribed to wolf/set on %s:%s",
            mqtt_client._wolf_mqtt_settings["host"],
            mqtt_client._wolf_mqtt_settings["port"],
        )

    mqtt_client.on_connect = _on_connect
    mqtt_client.on_message = _create_mqtt_set_handler(
        client, loop, gateway_id, system_id, parameters
    )
    _ensure_mqtt_connected(mqtt_client)
    mqtt_client.loop_start()
    return mqtt_client


def _build_status(parameters, values):
    status = {"time": datetime.now().strftime("%d/%m/%Y %H:%M:%S")}
    for val in values:
        par = next((x for x in parameters if x.value_id == val.value_id), None)
        if par is None:
            logging.debug("Skipping unknown value %s", val.value_id)
            continue
        status.setdefault(par.parent, {})[par.name] = val.value
    return status


def _publish_status(status, mqtt_client):
    if mqtt is None:
        logging.warning(
            "Skipping MQTT status publish because paho-mqtt is not installed."
        )
        return

    client = mqtt_client
    _ensure_mqtt_connected(client)
    try:
        client.publish("wolf/status", json.dumps(status), retain = True)
    except Exception:
        logging.exception("Failed to publish Wolf status via MQTT")
    finally:
        if not getattr(mqtt_client, "_wolf_persistent", False):
            client.disconnect()


def _fetch_and_log_status(client, loop, sl, pl, mqtt_client=None):
    print("Fetching parameters values")
    values = loop.run_until_complete(
        client.fetch_value(sl[0].gateway, sl[0].id, pl)
    )
    log_pretty("Parameter values", values, summarize_values)
    status = _build_status(pl, values)
    print("Sending output to MQTT")
    if mqtt_client:
        _publish_status(status, mqtt_client)
    print(status)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--set",
        nargs=2,
        metavar=("PARAMETER_NAME", "VALUE"),
        help="Parameter name and value to write before fetching status",
    )
    parser.add_argument(
        "--refresh_interval",
        type=int,
        nargs="?",
        const=60,
        default=None,
        help=(
            "Run in background refreshing status every N seconds; "
            "pass the interval value (defaults to 60 when flag is present without a value)."
        ),
    )

    args = parser.parse_args()
    client, loop, credentials = _build_client()
    sl, pl = _fetch_system_context(client, loop)
    if args.set:
        name, value = args.set
        _set_parameter(client, loop, sl[0].gateway, sl[0].id, pl, name, value)
    interval = args.refresh_interval
    mqtt_settings = _resolve_mqtt_settings(credentials["mqtt"])
    if interval is not None and mqtt_settings is None:
        raise ValueError(
            "Interval mode requires an MQTT server url in credentials.json under 'mqtt.url'."
        )
    mqtt_client = None
    if mqtt_settings:
        mqtt_client = _configure_mqtt_client(mqtt_settings)
    gateway_id = sl[0].gateway
    system_id = sl[0].id
    try:
        if interval is None:
            _fetch_and_log_status(
                client,
                loop,
                sl,
                pl,
                mqtt_client=mqtt_client,
            )
        else:
            mqtt_client = _start_mqtt_set_listener(
                client,
                loop,
                gateway_id,
                system_id,
                pl,
                mqtt_client,
            )
            while True:
                try:
                    _fetch_and_log_status(
                        client,
                        loop,
                        sl,
                        pl,
                        mqtt_client=mqtt_client,
                    )
                except Exception:
                    logging.exception("Failed to refresh status; retrying after sleep")
                if interval <= 0:
                    break
                print(f"Sleeping for {interval} seconds before next refresh")
                time.sleep(interval)
    except KeyboardInterrupt:
        print("Interrupted; stopping refresh loop.")
    finally:
        if mqtt_client is not None and getattr(mqtt_client, "_wolf_persistent", False):
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


if __name__ == "__main__":
    main()
