#!/usr/bin/env python3
import subprocess
import platform
import logging
import os

"""
If line out sound is muted with headphones connected:
    alsamixer
    F6 - select card
    right key - select Auto-Mute
    up - disable it
    Esc

"""



# Ensure Pulse can be reached when launched via shortcut
os.environ.setdefault(
    "XDG_RUNTIME_DIR",
    f"/run/user/{os.getuid()}"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class PulseAudioError(Exception):
    pass


def check_platform() -> None:
    if platform.system() != "Linux":
        raise PulseAudioError("This script only works on Linux.")


def run_pactl(*args: str) -> str:
    try:
        result = subprocess.run(
            ["pactl", *args],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise PulseAudioError(f"pactl {' '.join(args)} failed: {e}")


def get_default_sink_name() -> str:
    output = run_pactl("info")
    for line in output.splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()
    raise PulseAudioError("Could not determine default sink.")


def get_sink_index_and_port(sink_name: str) -> tuple[int, str]:
    output = run_pactl("list", "sinks")

    current_index: int | None = None
    current_name: str | None = None
    active_port: str | None = None

    for raw in output.splitlines():
        line = raw.strip()

        if line.startswith("Sink #"):
            current_index = int(line.split("#")[1])
            current_name = None
            active_port = None

        elif line.startswith("Name:"):
            current_name = line.split(":", 1)[1].strip()

        elif line.startswith("Active Port:"):
            active_port = line.split(":", 1)[1].strip()

            if current_name == sink_name and current_index is not None:
                return current_index, active_port

    raise PulseAudioError("Could not detect active port for default sink.")


def toggle_port_name(current: str) -> str:
    if current == "analog-output-lineout":
        return "analog-output-headphones"
    if current == "analog-output-headphones":
        return "analog-output-lineout"

    raise PulseAudioError(f"Unsupported port: {current}")


def set_sink_port(index: int, port: str) -> None:
    run_pactl("set-sink-port", str(index), port)
    logging.info(f"Switched sink #{index} to {port}")


def move_all_streams(index: int) -> None:
    output = run_pactl("list", "short", "sink-inputs")
    lines = output.splitlines()

    if not lines:
        logging.info("No active streams to move.")
        return

    for line in lines:
        stream_id = line.split()[0]
        run_pactl("move-sink-input", stream_id, str(index))
        logging.debug(f"Moved stream {stream_id} to sink {index}")


def main() -> None:
    check_platform()

    sink_name = get_default_sink_name()
    logging.info(f"Default sink: {sink_name}")

    index, current_port = get_sink_index_and_port(sink_name)
    logging.info(f"Current active port: {current_port}")

    new_port = toggle_port_name(current_port)
    set_sink_port(index, new_port)

    move_all_streams(index)

    logging.info(f"Toggle complete. Active port is now: {new_port}")


if __name__ == "__main__":
    try:
        main()
    except PulseAudioError as e:
        logging.error(e)
        raise
