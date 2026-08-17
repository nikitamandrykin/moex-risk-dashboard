from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

START_PORT = 8501
END_PORT = 8599
HOST = "127.0.0.1"
APP_FILE = Path(__file__).resolve().parent / "app.py"


def is_port_free(port: int, host: str = HOST) -> bool:
    """Return True when the TCP port can be bound locally."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(start: int = START_PORT, end: int = END_PORT) -> int:
    for port in range(start, end + 1):
        if is_port_free(port):
            return port
    raise RuntimeError(f"Не найден свободный порт в диапазоне {start}-{end}.")


def main() -> int:
    try:
        port = find_free_port()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        print("Закройте старые процессы Streamlit и повторите запуск.")
        return 1

    print()
    print("MOEX Risk Dashboard 1.0 Deployment Ready")
    print(f"Starting on http://localhost:{port}")
    if port != START_PORT:
        print(f"Port {START_PORT} is occupied, so a free port was selected automatically.")
    print("Press Ctrl+C to stop the dashboard.")
    print()

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_FILE),
        "--server.address",
        "localhost",
        "--server.port",
        str(port),
    ]
    return subprocess.call(command, cwd=str(APP_FILE.parent))


if __name__ == "__main__":
    raise SystemExit(main())
