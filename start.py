#!/usr/bin/env python3
"""
start.py — HanyaMusic launcher  (v2 — retry edition)
  1. Starts the FastAPI app via uvicorn (subprocess)
  2. Starts cloudflared tunnel, retrying up to MAX_CF_RETRIES times on failure
  3. Parses the trycloudflare.com URL from stdout OR stderr
  4. Pushes { "tunnel_url": "https://..." } to Firebase RTDB at /firebase.json
"""

import subprocess
import threading
import sys
import re
import time
import urllib.request
import urllib.error
import json
import signal

# ── Config ────────────────────────────────────────────────────────────────────
FIREBASE_URL   = "https://hanyamusic-ac4ce-default-rtdb.asia-southeast1.firebasedatabase.app/firebase.json"
LOCAL_PORT     = 8000
APP_MODULE     = "app:app"
VENV_PYTHON    = sys.executable   # honours whichever venv/python ran this script
MAX_CF_RETRIES = 10               # retry cloudflared this many times before giving up
CF_RETRY_DELAY = 5                # seconds to wait between retries

# ── Globals ───────────────────────────────────────────────────────────────────
_procs        = []
_tunnel_found = threading.Event()   # set once the URL has been pushed

# ── Firebase ──────────────────────────────────────────────────────────────────

def push_to_firebase(tunnel_url: str) -> None:
    payload = json.dumps({"tunnel_url": tunnel_url}).encode("utf-8")
    req = urllib.request.Request(
        FIREBASE_URL,
        data=payload,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                print(f"\n[FIREBASE] ✓ Pushed → {FIREBASE_URL}")
                print(f"[FIREBASE]   Response: {body}\n")
                return
        except urllib.error.URLError as e:
            print(f"[FIREBASE] ✗ Attempt {attempt}/3 failed: {e}")
            time.sleep(2)
    print("[FIREBASE] ✗ All attempts failed — URL NOT pushed to Firebase.")

# ── Stream reader ─────────────────────────────────────────────────────────────

URL_RE = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')

def _read_stream(stream, label: str) -> None:
    """Mirror cloudflared output to terminal; grab tunnel URL when it appears."""
    for raw in stream:
        line = raw.decode(errors="replace").rstrip()
        print(f"[cloudflared/{label}] {line}", flush=True)

        if not _tunnel_found.is_set():
            m = URL_RE.search(line)
            if m:
                tunnel_url = m.group(0)
                _tunnel_found.set()
                print(f"\n{'='*60}")
                print(f"  🌐 Tunnel URL: {tunnel_url}")
                print(f"{'='*60}\n")
                threading.Thread(
                    target=push_to_firebase, args=(tunnel_url,), daemon=True
                ).start()

# ── Uvicorn ───────────────────────────────────────────────────────────────────

def start_uvicorn() -> subprocess.Popen:
    cmd = [
        VENV_PYTHON, "-m", "uvicorn",
        APP_MODULE,
        "--host", "0.0.0.0",
        "--port", str(LOCAL_PORT),
        "--log-level", "info",
    ]
    print(f"[UVICORN] Starting: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
    _procs.append(proc)
    return proc

# ── Wait for uvicorn ──────────────────────────────────────────────────────────

def _wait_for_uvicorn(timeout: int = 30) -> bool:
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False

# ── Cloudflared (with retry) ──────────────────────────────────────────────────

def cloudflared_loop() -> None:
    print("[LAUNCHER] Waiting for uvicorn to accept connections...")
    if not _wait_for_uvicorn(timeout=30):
        print("[LAUNCHER] ⚠️  uvicorn didn't respond in 30 s — starting cloudflared anyway")

    for attempt in range(1, MAX_CF_RETRIES + 1):
        if _tunnel_found.is_set():
            return

        print(f"\n[cloudflared] ── Attempt {attempt}/{MAX_CF_RETRIES} ──────────────────────────")
        cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{LOCAL_PORT}"]

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _procs.append(proc)

            # Read both stdout and stderr concurrently
            threading.Thread(target=_read_stream, args=(proc.stdout, "out"), daemon=True).start()
            threading.Thread(target=_read_stream, args=(proc.stderr, "err"), daemon=True).start()

            # Poll until URL found or process exits
            while proc.poll() is None:
                if _tunnel_found.is_set():
                    break
                time.sleep(0.5)

            if _tunnel_found.is_set():
                proc.wait()   # keep alive until killed
                return

            exit_code = proc.returncode
            print(f"[cloudflared] ✗ Exited (code {exit_code}) without a tunnel URL")
            _procs.remove(proc)

        except FileNotFoundError:
            print("[cloudflared] ✗ 'cloudflared' binary not found in PATH.")
            print("   Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/")
            return

        if attempt < MAX_CF_RETRIES:
            print(f"[cloudflared] Retrying in {CF_RETRY_DELAY} s...")
            time.sleep(CF_RETRY_DELAY)

    print("[cloudflared] ✗ Exhausted all retries — tunnel not established.")

# ── Signal handler ────────────────────────────────────────────────────────────

def shutdown(signum, frame):
    print("\n[LAUNCHER] Shutting down...")
    for p in _procs:
        try:
            p.terminate()
        except Exception:
            pass
    sys.exit(0)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    uvicorn_proc = start_uvicorn()

    cf_thread = threading.Thread(target=cloudflared_loop, daemon=True)
    cf_thread.start()

    try:
        uvicorn_proc.wait()
    except KeyboardInterrupt:
        shutdown(None, None)

if __name__ == "__main__":
    main()