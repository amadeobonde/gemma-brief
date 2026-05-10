from __future__ import annotations
import base64
import logging
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import httpx
from podcastbrief.core.config import load_settings

log = logging.getLogger(__name__)

# Must EXACTLY match a Redirect URI registered in your Spotify Developer Dashboard.
REDIRECT_URI = "http://127.0.0.1:3000/discovery"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 3000
CALLBACK_PATH = "/discovery"

# Read scopes for playlists + episodes; tweak if you need write access later.
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class _CodeCatcher(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None
    expected_state: str = ""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        state = qs.get("state", [""])[0]
        if state != type(self).expected_state:
            type(self).error = "state mismatch"
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>State mismatch - auth aborted.</h2>")
            return
        if "error" in qs:
            type(self).error = qs["error"][0]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"<h2>Spotify returned: {type(self).error}</h2>".encode())
            return
        type(self).code = qs.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif;padding:40px;'>"
            b"<h2>Spotify auth complete.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, *args, **kwargs):  # silence default access log
        return


def run_spotify_auth() -> str:
    s = load_settings()
    if not s.spotify_client_id or not s.spotify_client_secret:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set before running auth."
        )

    state = secrets.token_urlsafe(16)
    _CodeCatcher.expected_state = state
    _CodeCatcher.code = None
    _CodeCatcher.error = None

    params = {
        "client_id": s.spotify_client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print(f"\nMake sure this redirect URI is registered in your Spotify app:\n  {REDIRECT_URI}\n", flush=True)
    print(f"Listening on http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}", flush=True)
    print("\n>>> OPEN THIS URL IN YOUR BROWSER <<<", flush=True)
    print(auth_url, flush=True)
    print("", flush=True)

    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CodeCatcher)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    try:
        while _CodeCatcher.code is None and _CodeCatcher.error is None:
            server.handle_request()
    finally:
        server.server_close()

    if _CodeCatcher.error:
        raise RuntimeError(f"Spotify auth failed: {_CodeCatcher.error}")
    code = _CodeCatcher.code
    if not code:
        raise RuntimeError("No authorization code received.")

    creds = base64.b64encode(
        f"{s.spotify_client_id}:{s.spotify_client_secret}".encode()
    ).decode()
    resp = httpx.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {creds}"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(f"No refresh_token in response: {body}")
    return refresh_token


def update_env_file(env_path: Path, refresh_token: str) -> None:
    """Replace or append SPOTIFY_REFRESH_TOKEN= in env_path."""
    env_path = Path(env_path)
    if not env_path.exists():
        env_path.write_text(f"SPOTIFY_REFRESH_TOKEN={refresh_token}\n", encoding="utf-8")
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("SPOTIFY_REFRESH_TOKEN="):
            out.append(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
