"""Compatibility entry point serving the frontend and backend on one port."""

from werkzeug.test import Client

from backend.app import EVENT_FIELDS as EVENT_FIELDS
from backend.app import create_app as create_backend
from frontend.__main__ import run
from frontend.app import create_app as create_frontend


def create_app(test_config=None, connection_factory=None):
    backend = create_backend(test_config, connection_factory)
    backend_api = backend.wsgi_app

    def local_request(method, path, headers, body=b"", query=b""):
        return Client(backend_api, use_cookies=False).open(
            path, method=method, headers=headers, data=body, query_string=query.decode("ascii")
        )

    frontend = create_frontend(local_request)

    def dispatch(environ, start_response):
        service = backend_api if environ["PATH_INFO"].startswith("/api/") else frontend.wsgi_app
        return service(environ, start_response)

    backend.wsgi_app = dispatch
    return backend


app = create_app()


if __name__ == "__main__":
    run(app)
