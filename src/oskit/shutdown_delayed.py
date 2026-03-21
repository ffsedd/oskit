#!/usr/bin/env python3

import os
import sys
import time
import fcntl
import subprocess

lock_path = f"/run/user/{os.getuid()}/sleep-lock"
DELAY = 3

# open lock file
lock_file = open(lock_path, "w")

try:
    # try to acquire non-blocking lock
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("Sleep already in progress.")
    time.sleep(2)
    sys.exit(1)

# check systemd jobs
result = subprocess.run(
    ["systemctl", "list-jobs"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True
)

if "suspend" in result.stdout or "shutdown" in result.stdout:
    print("A suspend/shutdown operation is already in progress.")
    time.sleep(2)
    sys.exit(1)

# countdown
for i in range(DELAY, 0, -1):
    print(f"Suspending in {i}...")
    time.sleep(1)

# suspend
subprocess.run(["systemctl", "suspend"])
