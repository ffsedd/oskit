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

def ping(ip: str) -> bool:
    """Return True if host responds to ping."""
    return subprocess.call(
        ["ping", "-c", "1", "-W", "1", ip],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0

def arp_mac(ip: str) -> str:
    """Return MAC address from ARP cache."""
    out = subprocess.check_output(["arp", "-n", ip], text=True)
    for ln in out.splitlines():
        if ip in ln:
            return ln.split()[2]
    return "-"

def netbios_name(ip: str) -> str:
    """Return NetBIOS hostname."""
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
    """Return mDNS hostname."""
    out = subprocess.check_output(
        ["avahi-resolve", "-a", ip],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return out.split()[1]

def dns_name(ip: str) -> str:
    """Return DNS hostname."""
    return socket.gethostbyaddr(ip)[0]

def hostname(ip: str) -> str:
    """Resolve hostname via NetBIOS, mDNS, then DNS."""
    for func in (netbios_name, mdns_name, dns_name):
        try:
            return func(ip)
        except Exception:
            continue
    return "-"

def port_open(ip: str, port: int, timeout: float) -> bool:
    """Return True if TCP port is open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((ip, port)) == 0

def services(ip: str, timeout: float) -> list[str]:
    """Return service codes in WEB, SMB, RDP, SSH, NX order."""
    return [
        name[0].upper() if any(port_open(ip, p, timeout) for p in ports) else "."
        for name, ports in PORTS.items()
    ]

def scan(ip: str, timeout: float) -> None:
    """Fast scan and print one host with padded columns."""
    if not ping(ip):
        return

    svc = services(ip, timeout)
    print(
        f"{ip:<15} {arp_mac(ip):<17} {' '.join(svc)} {hostname(ip)}"
    )

def main():
    parser = argparse.ArgumentParser(description="Fast LAN discovery with service codes.")
    parser.add_argument("-s", "--subnet", default="10.10.1", help="Subnet to scan (e.g., 10.10.1)")
    parser.add_argument("-t", "--threads", type=int, default=255, help="Number of parallel threads")
    parser.add_argument("--timeout", type=float, default=0.4, help="TCP connect timeout in seconds")
    args = parser.parse_args()

    ips = [f"{args.subnet}.{i}" for i in range(1, 256)]
    print(f"{'IP':<15} {'MAC':<17} WEB SMB RDP SSH NX HOSTNAME")

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        ex.map(lambda ip: scan(ip, args.timeout), ips)

if __name__ == "__main__":
    main()
