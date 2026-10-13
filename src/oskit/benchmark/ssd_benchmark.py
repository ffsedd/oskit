#!/usr/bin/env python3
"""Non-destructive SSD health and performance benchmark."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final


@dataclass(frozen=True)
class SmartData:
    passed: bool | None
    lifetime_percent: float | None
    temperature_c: float | None
    power_on_hours: int | None
    reallocated: int | None
    uncorrectable: int | None
    crc_errors: int | None
    program_failures: int | None
    erase_failures: int | None


@dataclass(frozen=True)
class BenchmarkResult:
    seq_read_mib: float | None
    seq_write_mib: float | None
    rand_read_iops: float | None
    rand_write_iops: float | None


@dataclass(frozen=True)
class DeviceInfo:
    device: str
    model: str
    size: str
    transport: str
    rotational: bool


@dataclass(frozen=True)
class Assessment:
    health: str
    performance: str
    recommendation: str
    reasons: tuple[str, ...]


REQUIRED_COMMANDS: Final = ("smartctl", "fio", "lsblk", "df")


def run_command(
    *args: str,
    sudo: bool = False,
    check: bool = True,
) -> str:
    command = list(args)

    if sudo:
        command.insert(0, "sudo")

    result = subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )

    return result.stdout


def check_dependencies() -> None:
    """Verify required external programs exist."""
    missing = [cmd for cmd in REQUIRED_COMMANDS if shutil.which(cmd) is None]

    if missing:
        names = " ".join(missing)
        raise RuntimeError(
            f"Missing commands: {names}\n"
            "Install with:\n"
            "  sudo apt install smartmontools fio"
        )


def get_device_info(device: str) -> DeviceInfo:
    """Read basic block-device information."""
    output = run_command(
        "lsblk",
        "-dn",
        "-o",
        "MODEL,SIZE,TRAN,ROTA",
        device,
    )

    fields = output.strip().split()

    if len(fields) < 4:
        raise RuntimeError(f"Could not parse lsblk output for {device!r}")

    model = " ".join(fields[:-3])
    size = fields[-3]
    transport = fields[-2]
    rotational = fields[-1] == "1"

    return DeviceInfo(
        device=device,
        model=model or "Unknown",
        size=size,
        transport=transport,
        rotational=rotational,
    )


def parse_int_attribute(text: str, name: str) -> int | None:
    """Extract a SMART raw integer attribute."""
    pattern = rf"^\s*\d+\s+{re.escape(name)}\s+.*?\s+(\d+)\s*$"

    for line in text.splitlines():
        match = re.match(pattern, line)

        if match:
            return int(match.group(1))

    return None


def parse_lifetime(text: str) -> float | None:
    """Extract SSD remaining lifetime percentage."""
    for line in text.splitlines():
        if "Remaining_Lifetime_Perc" not in line:
            continue

        fields = line.split()

        if fields:
            try:
                return float(fields[-1])
            except ValueError:
                return None

    return None


def parse_temperature(text: str) -> float | None:
    """Extract SMART temperature."""
    for line in text.splitlines():
        if "Temperature_Celsius" not in line:
            continue

        fields = line.split()

        if fields:
            try:
                return float(fields[-1])
            except ValueError:
                return None

    return None


def parse_smart(text: str) -> SmartData:
    """Parse SMART attributes."""
    health_match = re.search(
        r"SMART overall-health self-assessment test result:\s*(\w+)",
        text,
    )

    passed: bool | None = None

    if health_match:
        passed = health_match.group(1).upper() == "PASSED"

    return SmartData(
        passed=passed,
        lifetime_percent=parse_lifetime(text),
        temperature_c=parse_temperature(text),
        power_on_hours=parse_int_attribute(text, "Power_On_Hours"),
        reallocated=parse_int_attribute(text, "Reallocated_Sector_Ct"),
        uncorrectable=parse_int_attribute(text, "Uncorrectable_Error_Cnt"),
        crc_errors=parse_int_attribute(text, "UDMA_CRC_Error_Count"),
        program_failures=parse_int_attribute(text, "Program_Fail_Cnt_Total"),
        erase_failures=parse_int_attribute(text, "Erase_Fail_Count_Total"),
    )


def get_smart(device: str) -> SmartData:
    """Read SMART data."""
    output = run_command("smartctl", "-x", device, sudo=True, check=False)
    return parse_smart(output)


def parse_fio_value(output: str, pattern: str) -> float | None:
    """Extract the first numeric value matching a fio pattern."""
    match = re.search(pattern, output, re.MULTILINE)

    if not match:
        return None

    return float(match.group(1))


def run_fio(
    filename: Path,
    size: str,
    read_write: str,
    block_size: str,
    runtime: int | None = None,
) -> str:
    """Run one fio benchmark."""
    command = [
        "fio",
        "--name=ssd-test",
        f"--filename={filename}",
        f"--size={size}",
        f"--rw={read_write}",
        f"--bs={block_size}",
        "--ioengine=libaio",
        "--direct=1",
        "--iodepth=32" if "rand" in read_write else "--iodepth=16",
        "--numjobs=1",
        "--group_reporting",
    ]

    if runtime is not None:
        command.extend(
            [
                f"--runtime={runtime}",
                "--time_based",
            ]
        )

    return run_command(*command)


def benchmark(test_dir: Path, size: str) -> BenchmarkResult:
    """Run sequential and random I/O benchmarks."""
    test_file = test_dir / "ssd-benchmark.bin"

    seq_write = run_fio(
        test_file,
        size,
        "write",
        "1M",
    )

    seq_read = run_fio(
        test_file,
        size,
        "read",
        "1M",
    )

    rand_read = run_fio(
        test_file,
        size,
        "randread",
        "4k",
        runtime=30,
    )

    rand_write = run_fio(
        test_file,
        size,
        "randwrite",
        "4k",
        runtime=30,
    )

    return BenchmarkResult(
        seq_read_mib=parse_fio_value(
            seq_read,
            r"\bread: IOPS=.*?BW=([\d.]+)MiB",
        ),
        seq_write_mib=parse_fio_value(
            seq_write,
            r"\bwrite: IOPS=.*?BW=([\d.]+)MiB",
        ),
        rand_read_iops=parse_fio_value(
            rand_read,
            r"\bread: IOPS=([\d.]+)k",
        ),
        rand_write_iops=parse_fio_value(
            rand_write,
            r"\bwrite: IOPS=([\d.]+)k",
        ),
    )


def assess_health(smart: SmartData) -> tuple[str, tuple[str, ...]]:
    """Assess SSD health."""
    reasons: list[str] = []

    if smart.passed is False:
        return "CRITICAL", ("SMART self-assessment FAILED",)

    critical_attributes = (
        ("reallocated sectors", smart.reallocated),
        ("uncorrectable errors", smart.uncorrectable),
        ("CRC errors", smart.crc_errors),
        ("program failures", smart.program_failures),
        ("erase failures", smart.erase_failures),
    )

    critical = [
        name for name, value in critical_attributes if value is not None and value > 0
    ]

    if critical:
        return "WARNING", tuple(f"{name}: non-zero" for name in critical)

    lifetime = smart.lifetime_percent

    if lifetime is not None:
        if lifetime <= 10:
            return "CRITICAL", (f"only {lifetime:.0f}% lifetime remaining",)

        if lifetime <= 20:
            return "WARNING", (f"only {lifetime:.0f}% lifetime remaining",)

        if lifetime >= 80:
            reasons.append(f"{lifetime:.0f}% estimated lifetime remaining")
        else:
            reasons.append(f"{lifetime:.0f}% estimated lifetime remaining")

    if smart.temperature_c is not None:
        if smart.temperature_c >= 70:
            return "WARNING", (
                *reasons,
                f"temperature {smart.temperature_c:.0f} °C",
            )

        reasons.append(f"temperature {smart.temperature_c:.0f} °C")

    if smart.passed is True:
        reasons.insert(0, "SMART PASSED")

    return "EXCELLENT", tuple(reasons)


def assess_performance(
    device: DeviceInfo,
    benchmark_result: BenchmarkResult,
) -> tuple[str, tuple[str, ...]]:
    """Assess performance using interface-aware thresholds."""
    reasons: list[str] = []

    if device.transport.lower() == "sata":
        seq_read_good = 400
        seq_write_good = 200
    else:
        seq_read_good = 1500
        seq_write_good = 800

    if benchmark_result.seq_read_mib is not None:
        read = benchmark_result.seq_read_mib

        if read >= seq_read_good:
            reasons.append(f"{read:.0f} MiB/s sequential read")
        else:
            reasons.append(f"{read:.0f} MiB/s sequential read")

    if benchmark_result.seq_write_mib is not None:
        write = benchmark_result.seq_write_mib
        reasons.append(f"{write:.0f} MiB/s sequential write")

    if benchmark_result.rand_read_iops is not None:
        reasons.append(f"{benchmark_result.rand_read_iops * 1000:.0f} 4K read IOPS")

    if benchmark_result.rand_write_iops is not None:
        reasons.append(f"{benchmark_result.rand_write_iops * 1000:.0f} 4K write IOPS")

    read_ok = (
        benchmark_result.seq_read_mib is None
        or benchmark_result.seq_read_mib >= seq_read_good
    )

    write_ok = (
        benchmark_result.seq_write_mib is None
        or benchmark_result.seq_write_mib >= seq_write_good
    )

    random_read_ok = (
        benchmark_result.rand_read_iops is None or benchmark_result.rand_read_iops >= 30
    )

    random_write_ok = (
        benchmark_result.rand_write_iops is None
        or benchmark_result.rand_write_iops >= 30
    )

    if read_ok and write_ok and random_read_ok and random_write_ok:
        return "VERY GOOD", tuple(reasons)

    if read_ok and write_ok:
        return "GOOD", tuple(reasons)

    return "SLOW", tuple(reasons)


def assess(
    device: DeviceInfo,
    smart: SmartData,
    benchmark_result: BenchmarkResult,
) -> Assessment:
    """Create final SSD assessment."""
    health, health_reasons = assess_health(smart)
    performance, performance_reasons = assess_performance(
        device,
        benchmark_result,
    )

    if health == "CRITICAL":
        recommendation = "REPLACE"
    elif health == "WARNING":
        recommendation = "PLAN REPLACEMENT"
    elif performance == "SLOW":
        recommendation = "INVESTIGATE"
    else:
        recommendation = "KEEP"

    reasons = (
        *health_reasons,
        *performance_reasons,
    )

    return Assessment(
        health=health,
        performance=performance,
        recommendation=recommendation,
        reasons=reasons,
    )


def print_report(
    device: DeviceInfo,
    smart: SmartData,
    benchmark_result: BenchmarkResult,
    assessment: Assessment,
) -> None:
    """Print human-readable report."""
    print()
    print("=" * 60)
    print("SSD HEALTH & PERFORMANCE REPORT")
    print("=" * 60)

    print(f"Device:          {device.device}")
    print(f"Model:           {device.model}")
    print(f"Size:            {device.size}")
    print(f"Interface:       {device.transport.upper()}")
    print()

    print("HEALTH")
    print(f"  Status:        {assessment.health}")

    if smart.passed is not None:
        print(f"  SMART:         {'PASSED' if smart.passed else 'FAILED'}")

    if smart.lifetime_percent is not None:
        print(f"  Lifetime:      {smart.lifetime_percent:.0f}%")

    if smart.temperature_c is not None:
        print(f"  Temperature:   {smart.temperature_c:.0f} °C")

    if smart.power_on_hours is not None:
        print(f"  Power-on:      {smart.power_on_hours:,} h")

    print()
    print("PERFORMANCE")
    print(f"  Status:        {assessment.performance}")

    if benchmark_result.seq_read_mib is not None:
        print(f"  Seq. read:     {benchmark_result.seq_read_mib:.0f} MiB/s")

    if benchmark_result.seq_write_mib is not None:
        print(f"  Seq. write:    {benchmark_result.seq_write_mib:.0f} MiB/s")

    if benchmark_result.rand_read_iops is not None:
        print(f"  4K read:       {benchmark_result.rand_read_iops * 1000:.0f} IOPS")

    if benchmark_result.rand_write_iops is not None:
        print(f"  4K write:      {benchmark_result.rand_write_iops * 1000:.0f} IOPS")

    print()
    print("RECOMMENDATION")
    print(f"  >>> {assessment.recommendation} <<<")
    print()

    for reason in assessment.reasons:
        print(f"  - {reason}")

    print("=" * 60)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Non-destructive SSD benchmark and evaluation.",
    )

    parser.add_argument(
        "device",
        help="Block device, e.g. /dev/sda",
    )

    parser.add_argument(
        "--size",
        default="2G",
        help="Benchmark file size (default: 2G)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON",
    )

    return parser.parse_args()


def main() -> int:
    """Run SSD diagnostic."""
    args = parse_args()

    try:
        check_dependencies()

        device = get_device_info(args.device)
        smart = get_smart(args.device)

        with tempfile.TemporaryDirectory(
            prefix="ssd-benchmark-",
        ) as tmp:
            benchmark_result = benchmark(
                Path(tmp),
                args.size,
            )

        assessment = assess(
            device,
            smart,
            benchmark_result,
        )

        if args.json:
            result = {
                "device": device.device,
                "model": device.model,
                "size": device.size,
                "transport": device.transport,
                "health": {
                    "status": assessment.health,
                    "smart_passed": smart.passed,
                    "lifetime_percent": smart.lifetime_percent,
                    "temperature_c": smart.temperature_c,
                    "power_on_hours": smart.power_on_hours,
                    "reallocated": smart.reallocated,
                    "uncorrectable": smart.uncorrectable,
                    "crc_errors": smart.crc_errors,
                },
                "performance": {
                    "status": assessment.performance,
                    "seq_read_mib": benchmark_result.seq_read_mib,
                    "seq_write_mib": benchmark_result.seq_write_mib,
                    "rand_read_iops": (
                        benchmark_result.rand_read_iops * 1000
                        if benchmark_result.rand_read_iops is not None
                        else None
                    ),
                    "rand_write_iops": (
                        benchmark_result.rand_write_iops * 1000
                        if benchmark_result.rand_write_iops is not None
                        else None
                    ),
                },
                "recommendation": assessment.recommendation,
                "reasons": assessment.reasons,
            }

            print(json.dumps(result, indent=2))
        else:
            print_report(
                device,
                smart,
                benchmark_result,
                assessment,
            )

    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
