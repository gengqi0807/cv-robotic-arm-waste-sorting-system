"""Send requests to a fixed backend service."""

from http.client import HTTPConnection, HTTPException, HTTPSConnection
from urllib.parse import quote, quote_from_bytes, urlsplit

from flask import Response
from werkzeug.datastructures import Headers
from werkzeug.http import remove_hop_by_hop_headers


class BackendUnavailable(RuntimeError):
    pass


class BackendClient:
    def __init__(self, base_url: str):
        url = urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("BACKEND_URL 必须是 HTTP 或 HTTPS 服务地址，例如 http://127.0.0.1:5002。")
        self.host = url.hostname
        self.authority = url.netloc
        self.port = url.port
        self.origin = f"{url.scheme}://{url.netloc}"
        self.connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection

    def __call__(self, method: str, path: str, headers: Headers, body: bytes = b"", query: bytes = b"") -> Response:
        # Browser origins are validated by the frontend before forwarding.
        headers = headers.copy()
        headers["Host"] = self.authority
        if "Origin" in headers:
            headers["Origin"] = self.origin
        target = quote(path, safe="/")
        if query:
            target += "?" + quote_from_bytes(query, safe="!$&'()*+,-./:;=?@_%~")
        connection = self.connection_type(self.host, self.port, timeout=10)
        try:
            connection.request(method, target, body=body or None, headers=dict(headers))
            upstream = connection.getresponse()
            response_headers = upstream.getheaders()
            connection_headers = {name.strip().lower() for name in upstream.headers.get("Connection", "").split(",")}
            response_headers = [(name, value) for name, value in response_headers
                                if name.lower() not in connection_headers]
            remove_hop_by_hop_headers(response_headers)
            return Response(upstream.read(), status=upstream.status, headers=response_headers)
        except (OSError, HTTPException) as error:
            raise BackendUnavailable("无法连接后端 API。") from error
        finally:
            connection.close()
