#!/usr/bin/env python3
import serial
import time

PORT = "/dev/ttyUSB0"
BAUDS = [115200, 57600, 38400, 19200, 9600]
SECONDS_PER_BAUD = 3
ASCII_THRESHOLD = 0.7

def ascii_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    printable = sum(32 <= b <= 126 or b in (9,10,13) for b in data)
    return printable / len(data)


def main():
    print(f"Scanning {PORT} for readable baud rates...")
    print("Tip: You can check UART parameters with 'setserial -g /dev/ttyUSB0'")
    print("or configure line settings if needed.\n")

    results = []
    detected_baud = None

    for baud in BAUDS:
        print(f"\n===== Trying {PORT} @ {baud} baud =====")
        try:
            with serial.Serial(PORT, baudrate=baud, timeout=0.1) as ser:
                # discard first read
                if ser.in_waiting:
                    _ = ser.read(ser.in_waiting)

                start = time.time()
                buffer = bytearray()
                while time.time() - start < SECONDS_PER_BAUD:
                    if ser.in_waiting:
                        buffer += ser.read(ser.in_waiting)

                ratio = ascii_ratio(buffer)
                results.append((baud, ratio))

                if ratio > ASCII_THRESHOLD:
                    print(f"[LIKELY READABLE] ASCII ratio: {ratio:.2f}")
                    print(buffer.decode("utf-8", errors="replace"))
                    detected_baud = baud
                    break  # stop scanning lower baud rates
                else:
                    print(f"Unreadable data (ASCII ratio {ratio:.2f})")
        except serial.SerialException as e:
            print(f"Error opening {PORT} at {baud} baud: {e}")
            results.append((baud, 0.0))

    # summary
    print("\n===== Summary =====")
    for baud, ratio in results:
        status = "READABLE" if ratio > ASCII_THRESHOLD else "UNREADABLE"
        print(f"{PORT} @ {baud:>6} baud → {status} (ASCII ratio {ratio:.2f})")

    if detected_baud:
        print("\nDetected likely readable baud rate:", detected_baud)
        print("You can set the port to this baud using:")
        print(f"  stty -F {PORT} {detected_baud} cs8 -cstopb -parenb  # set baud for USB-serial")
        print(f"  cat {PORT} ")
        print(f"  cutecom ")
    else:
        print("\nNo readable baud rate detected.")


if __name__ == "__main__":
    main()