"""Exercise the pinned CPU server over its loopback transcription protocol."""

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def smoke(binary, model, sample):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    # Do not route the loopback-only inference protocol through an inherited proxy.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(
            [
                str(binary),
                "--model",
                str(model),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--threads",
                "2",
                "--language",
                "en",
                "--max-len",
                "4096",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("Whisper server exited before becoming ready")
                try:
                    with client.open(f"{endpoint}/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.5)
            else:
                raise TimeoutError("Whisper server did not become ready")
            boundary = "openwhispr-package-validation"
            body = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\n'
                    "Content-Type: audio/wav\r\n\r\n"
                ).encode()
                + sample.read_bytes()
                + f"\r\n--{boundary}--\r\n".encode()
            )
            request = urllib.request.Request(
                f"{endpoint}/inference",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            with client.open(request, timeout=180) as response:
                result = json.load(response)
            text = result.get("text", "").lower()
            if "country" not in text or "ask" not in text:
                raise ValueError(f"Unexpected sample transcription: {result}")
            print("Pinned CPU Whisper server transcribed the upstream speech sample:", text.strip())
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            log.seek(0)
            print(log.read())


if __name__ == "__main__":
    smoke(*(Path(argument).resolve(strict=True) for argument in sys.argv[1:]))
