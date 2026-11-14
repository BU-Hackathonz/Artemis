"""One-time voice setup on the demo laptop, before venue Wi-Fi matters.

    cd server && uv run --group voice python -m artemis.voice.setup

1. Downloads NVDA's controller client from NV Access, checks its pinned SHA-256, and extracts
   the x64 DLL (64-bit Python can't load the x86 one) to voice/bin/.
2. Downloads and loads the faster-whisper model (ARTEMIS_WHISPER_MODEL, default small.en) so the
   first push-to-talk doesn't stall.
"""

from __future__ import annotations

import hashlib
import io
import time
import urllib.request
import zipfile

from .speech import NVDA_DLL, NvdaSpeaker, nvda_running

NVDA_VERSION = "2026.2"
CLIENT_URL = f"https://download.nvaccess.org/releases/{NVDA_VERSION}/nvda_{NVDA_VERSION}_controllerClient.zip"
CLIENT_SHA256 = "510736f021aefa33378076fa342a4524b586bc1c6b096db9479af42b28aac649"


def fetch_nvda_dll() -> None:
    if NVDA_DLL.is_file():
        print(f"NVDA controller DLL already at {NVDA_DLL}")
    else:
        print(f"Downloading {CLIENT_URL}")
        with urllib.request.urlopen(CLIENT_URL, timeout=60) as r:
            data = r.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != CLIENT_SHA256:
            raise SystemExit(f"Checksum mismatch for the NVDA controller client: got {digest}. Not installing it.")
        NVDA_DLL.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            NVDA_DLL.write_bytes(z.read("x64/nvdaControllerClient.dll"))
        print(f"Installed {NVDA_DLL}")
    if NvdaSpeaker().available():
        print("NVDA is running and reachable through the DLL.")
    elif nvda_running():
        raise SystemExit("NVDA is running but the DLL won't load. Check the log above.")
    else:
        print("NVDA isn't running now; Artemis will use SAPI until it starts.")


def fetch_model() -> None:
    from faster_whisper import WhisperModel

    from .listen import whisper_model

    model = whisper_model()  # the same one the core will load
    t = time.time()
    WhisperModel(model, device="cpu", compute_type="int8")
    print(f"Whisper {model} ready ({time.time() - t:.1f} s).")


if __name__ == "__main__":
    fetch_nvda_dll()
    fetch_model()
