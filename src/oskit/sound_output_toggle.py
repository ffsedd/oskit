#!/usr/bin/env python3
import subprocess
import platform
import logging

SINK = 0  # your sink number

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class PulseAudioError(Exception):
    pass

def check_platform():
    if platform.system() != "Linux":
        raise PulseAudioError("This script only works on Linux with PulseAudio.")

def get_sinks_output() -> str:
    """Capture the full output of `pactl list sinks`."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sinks"],
            text=True,
            capture_output=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise PulseAudioError(f"Failed to list sinks: {e}")

def get_active_port(sinks_output: str, sink: int) -> str:
    """Parse the active port for a given sink."""
    lines = sinks_output.splitlines()
    found_sink = False
    for line in lines:
        line = line.strip()
        if line.startswith(f"Sink #{sink}"):
            found_sink = True
        elif found_sink and line.startswith("Active Port:"):
            return line.split(":", 1)[1].strip()
    raise PulseAudioError("Could not detect active port. Make sure PulseAudio is running.")

def toggle_port(current_port: str) -> str:
    """Decide the new port to switch to."""
    return "analog-output-headphones" if current_port == "analog-output-lineout" else "analog-output-lineout"

def set_sink_port(sink: int, port: str):
    try:
        subprocess.run(["pactl", "set-sink-port", str(sink), port], check=True)
        logging.info(f"Switched sink #{sink} to {port}")
    except subprocess.CalledProcessError as e:
        raise PulseAudioError(f"Failed to set sink port: {e}")

def move_all_streams(sink: int):
    """Move all current streams to the new sink."""
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sink-inputs"],
            text=True,
            capture_output=True,
            check=True
        )
        streams = result.stdout.splitlines()
        if not streams:
            logging.info("No active streams to move.")
        for line in streams:
            stream_id = line.split()[0]
            subprocess.run(["pactl", "move-sink-input", stream_id, str(sink)], check=True)
            logging.debug(f"Moved stream {stream_id} to sink {sink}")
    except subprocess.CalledProcessError as e:
        raise PulseAudioError(f"Failed to move sink inputs: {e}")

def main():
    check_platform()
    sinks_output = get_sinks_output()
    current = get_active_port(sinks_output, SINK)
    logging.info(f"Current active port: {current}")
    new_port = toggle_port(current)
    set_sink_port(SINK, new_port)
    move_all_streams(SINK)
    logging.info(f"Toggle complete. Active port is now: {new_port}")

if __name__ == "__main__":
    try:
        main()
    except PulseAudioError as e:
        logging.error(e)

