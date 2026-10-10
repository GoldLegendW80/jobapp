"""A stand-in for the one Anthropic API call the credential check makes, for e2e runs only.

POST /api/me/model-credential/verify lists one model (GET /v1/models?limit=1) with the user's
key in X-Api-Key (api/app/agents/verify.py). With MODEL_API_BASE_URL=http://anthropicstub:8080
(tests/anthropic-stub.compose.yml, refused when APP_ENV is production) the api asks this server
instead, so the journey never sends a key to Anthropic. The answer depends on the key:
- containing "e2egood": 200 with an empty model list (verified);
- containing "e2ebusy": 429 with Retry-After: 5 (try again later);
- anything else: 401 (invalid credential).
/healthz answers 200 for the Compose healthcheck. Nothing is logged about the keys.

    python3 tests/anthropic_stub.py [port]        default port 8080, standard library only
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return  # never print request lines (they are harmless, but keep the logs quiet)

    def _send(
        self, status: int, body: object, headers: dict[str, str] | None = None
    ) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send(200, {"ok": True})
            return
        if path != "/v1/models":
            self._send(404, {"type": "error", "error": {"type": "not_found_error"}})
            return
        key = self.headers.get("x-api-key", "")
        if "e2egood" in key:
            self._send(
                200, {"data": [], "has_more": False, "first_id": None, "last_id": None}
            )
        elif "e2ebusy" in key:
            self._send(
                429,
                {
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "slow down"},
                },
                {"Retry-After": "5"},
            )
        else:
            self._send(
                401,
                {
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid"},
                },
            )


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
