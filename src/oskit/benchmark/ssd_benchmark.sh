#!/usr/bin/env bash
set -euo pipefail

DISK="${1:-/dev/sda}"
TEST_SIZE="${TEST_SIZE:-4G}"
TEST_DIR="${TEST_DIR:-/tmp/ssd-benchmark}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 ||
        die "Required command not found: $1"
}

echo "=== SSD Benchmark ==="
echo "Disk:      $DISK"
echo "Test size: $TEST_SIZE"
echo "Test dir:  $TEST_DIR"
echo

[[ -b "$DISK" ]] || die "$DISK is not a block device"

for cmd in lsblk smartctl fio awk grep sed df sync; do
    need_cmd "$cmd"
done

echo "=== DEVICE ==="
lsblk -d -o NAME,MODEL,SERIAL,SIZE,ROTA,TRAN "$DISK"
echo

echo "=== PARTITIONS ==="
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS "$DISK"
echo

echo "=== SMART HEALTH ==="
sudo smartctl -H "$DISK" || true
echo

echo "=== SMART INFORMATION ==="
sudo smartctl -A "$DISK" || true
echo

echo "=== TRIM SUPPORT ==="
if command -v lsblk >/dev/null && lsblk -D "$DISK" 2>/dev/null; then
    :
else
    echo "Could not determine TRIM support."
fi
echo

echo "=== FILESYSTEM TEST ==="

mkdir -p "$TEST_DIR"

available_kib=$(df --output=avail -k "$TEST_DIR" | tail -1 | tr -d ' ')
required_kib=$(numfmt --from=iec "$TEST_SIZE" 2>/dev/null | awk '{print int($1 / 1024)}')

if (( available_kib < required_kib + 1048576 )); then
    echo "Not enough free space for $TEST_SIZE test."
    echo "Reduce TEST_SIZE, e.g.:"
    echo
    echo "    TEST_SIZE=1G $0 $DISK"
    exit 1
fi

TEST_FILE="$TEST_DIR/ssd-test.bin"

cleanup() {
    rm -f "$TEST_FILE"
}
trap cleanup EXIT

echo
echo "--- Sequential write ---"
fio \
    --name=seq-write \
    --filename="$TEST_FILE" \
    --size="$TEST_SIZE" \
    --rw=write \
    --bs=1M \
    --ioengine=libaio \
    --direct=1 \
    --iodepth=16 \
    --numjobs=1 \
    --group_reporting

sync

echo
echo "--- Sequential read ---"
fio \
    --name=seq-read \
    --filename="$TEST_FILE" \
    --size="$TEST_SIZE" \
    --rw=read \
    --bs=1M \
    --ioengine=libaio \
    --direct=1 \
    --iodepth=16 \
    --numjobs=1 \
    --group_reporting

echo
echo "--- Random 4K read ---"
fio \
    --name=rand-read \
    --filename="$TEST_FILE" \
    --size="$TEST_SIZE" \
    --rw=randread \
    --bs=4k \
    --ioengine=libaio \
    --direct=1 \
    --iodepth=32 \
    --numjobs=1 \
    --runtime=30 \
    --time_based \
    --group_reporting

echo
echo "--- Random 4K write ---"
fio \
    --name=rand-write \
    --filename="$TEST_FILE" \
    --size="$TEST_SIZE" \
    --rw=randwrite \
    --bs=4k \
    --ioengine=libaio \
    --direct=1 \
    --iodepth=32 \
    --numjobs=1 \
    --runtime=30 \
    --time_based \
    --group_reporting

echo
echo "=== FINAL SMART HEALTH ==="
sudo smartctl -H "$DISK" || true

echo
echo "Benchmark complete."
