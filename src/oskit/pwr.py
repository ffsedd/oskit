#!/usr/bin/env python3

import os
import sys
import time
import fcntl
import subprocess

# ---------------- CONFIG ----------------
DEFAULT_ACTION = "suspend"
DEFAULT_DELAY = 3

ACTIONS = {
    "suspend": "suspend",
    "sleep": "suspend",
    "off": "poweroff",
    "shutdown": "poweroff",
    "restart": "reboot",
    "reboot": "reboot",
}
# ----------------------------------------

lock_path = f"/run/user/{os.getuid()}/pwr-lock"


def parse_args():
    action = DEFAULT_ACTION
    delay = DEFAULT_DELAY

    if len(sys.argv) >= 2:
        arg1 = sys.argv[1].lower()

        if arg1.isdigit():
            delay = int(arg1)
        else:
            action = ACTIONS.get(arg1, None)
            if action is None:
                print(f"Unknown action: {arg1}")
                sys.exit(1)

    if len(sys.argv) >= 3:
        if sys.argv[2].isdigit():
            delay = int(sys.argv[2])
        else:
            print("Delay must be a number")
            sys.exit(1)

    return action, delay


def acquire_lock():
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Power action already in progress.")
        time.sleep(2)
        sys.exit(1)
    return lock_file


def check_systemd():
    result = subprocess.run(
        ["systemctl", "list-jobs"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True
    )

    if any(x in result.stdout for x in ["suspend", "shutdown", "poweroff", "reboot"]):
        print("Another power operation is already in progress.")
        time.sleep(2)
        sys.exit(1)


def countdown(action, delay):
    if delay <= 0:
        return

    for i in range(delay, 0, -1):
        print(f"{action.capitalize()} in {i}...")
        time.sleep(1)


def run_action(action):
    print(f"Executing: {action}")
    subprocess.run(["systemctl", action])


def main():
    try:
        action, delay = parse_args()
        acquire_lock()
        check_systemd()
        countdown(action, delay)
        run_action(action)
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(130)

if __name__ == "__main__":
	main()

