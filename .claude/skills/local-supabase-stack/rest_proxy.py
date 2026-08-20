"""Minimal reverse proxy: /rest/v1/* -> PostgREST root.

supabase-js's createClient() (what web/src/lib/supabase.ts actually uses)
unconditionally assumes a full Supabase deployment's Kong-gateway routing
(/rest/v1/...), which a bare PostgREST instance doesn't have. Not part of
any shipped code -- lets the REAL route handler be tested via a REAL HTTP
client against a REAL local Postgres, without changing anything about how
the route itself talks to Supabase.

Usage: python3 rest_proxy.py
Forwards http://127.0.0.1:3021/rest/v1/* -> http://127.0.0.1:3020/*
(adjust POSTGREST_URL/LISTEN_PORT below if the local stack uses different
ports than the local-supabase-stack skill's default).
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import urllib.error

POSTGREST_URL = "http://127.0.0.1:3020"
LISTEN_PORT = 3021


class ProxyHandler(BaseHTTPRequestHandler):
    def _forward(self, method):
        path = self.path
        if path.startswith("/rest/v1"):
            path = path[len("/rest/v1"):] or "/"
        url = POSTGREST_URL + path
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code)
            for k, v in exc.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(exc.read())

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_PATCH(self):
        self._forward("PATCH")

    def do_DELETE(self):
        self._forward("DELETE")

    def log_message(self, format, *args):
        print(f"[proxy] {self.address_string()} {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    print(f"[proxy] listening on :{LISTEN_PORT}, forwarding /rest/v1/* -> {POSTGREST_URL}")
    server.serve_forever()
