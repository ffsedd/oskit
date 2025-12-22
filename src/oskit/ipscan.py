#!/usr/bin/env python3
"""Fast LAN discovery with aligned columns and hostname last, using argparse."""

import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
import argparse

PORTS = {
    "web": (80, 443),
    "smb": (445,),
    "rdp": (3389,),
    "nx":  (4000,),
    "ssh": (22,),
}


def guess_subnet() -> str:
    """
    Guess local /24 subnet from primary IPv4 address.
    Example: 192.168.1.42 -> 192.168.1
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()

    return ".".join(ip.split(".")[:3])


def ping(ip: str) -> bool:
    return subprocess.call(
        ["ping", "-c", "1", "-W", "1", ip],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


def arp_mac(ip: str) -> str:
    out = subprocess.check_output(["arp", "-n", ip], text=True)
    for ln in out.splitlines():
        if ip in ln:
            return ln.split()[2]
    return "-"


def netbios_name(ip: str) -> str:
    out = subprocess.check_output(
        ["nmblookup", "-A", ip],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    for ln in out.splitlines():
        if "<00>" in ln and "GROUP" not in ln:
            return ln.split("<00>")[0].strip()
    return ""


def mdns_name(ip: str) -> str:
    out = subprocess.check_output(
        ["avahi-resolve", "-a", ip],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return out.split()[1]


def dns_name(ip: str) -> str:
    return socket.gethostbyaddr(ip)[0]


def hostname(ip: str) -> str:
    for func in (netbios_name, mdns_name, dns_name):
        try:
            return func(ip)
        except Exception:
            continue
    return "-"


def port_open(ip: str, port: int, timeout: float) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((ip, port)) == 0


def services(ip: str, timeout: float) -> list[str]:
    return [
        name[0].upper() if any(port_open(ip, p, timeout) for p in ports) else "."
        for name, ports in PORTS.items()
    ]


def scan(ip: str, timeout: float) -> None:
    if not ping(ip):
        return

    svc = services(ip, timeout)
    print(f"{ip:<15} {arp_mac(ip):<17} {' '.join(svc)} {hostname(ip)}")


def main():
    parser = argparse.ArgumentParser(description="Fast LAN discovery with service codes.")
    parser.add_argument(
        "-s", "--subnet",
        help="Subnet to scan (e.g., 192.168.1). If omitted, guessed from local IP."
    )
    parser.add_argument("-t", "--threads", type=int, default=255)
    parser.add_argument("--timeout", type=float, default=0.4)
    args = parser.parse_args()

    subnet = args.subnet or guess_subnet()
    ips = [f"{subnet}.{i}" for i in range(1, 256)]

    print(f"{'IP':<15} {'MAC':<17} WEB SMB RDP SSH NX HOSTNAME")

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        ex.map(lambda ip: scan(ip, args.timeout), ips)


if __name__ == "__main__":
    main()

