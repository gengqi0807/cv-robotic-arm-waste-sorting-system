"""Frontend entry point; backend and face models live in RobotArm-a."""
import os
from pathlib import Path
import sys

# Keep the historical create_app factory for integration tests and diagnostics.
# Running app.py serves only the frontend; all /api calls go to BACKEND_URL.
api_root = Path(os.environ.get('ROBOT_WEB_API_ROOT',
    str(Path(__file__).resolve().parent.parent / 'robotic-arm/RobotArm-a/web_api')))
if api_root.is_dir():
    sys.path.insert(0, str(api_root))

from werkzeug.test import Client

from frontend.__main__ import run
from frontend.app import create_app as create_frontend


def create_app(test_config=None, connection_factory=None):
    from backend.app import create_app as create_backend
    backend = create_backend(test_config, connection_factory)
    backend_api = backend.wsgi_app

    def local_request(method, path, headers, body=b"", query=b""):
        return Client(backend_api, use_cookies=False).open(
            path, method=method, headers=headers, data=body, query_string=query.decode("ascii")
        )

    frontend = create_frontend(local_request)
    frontend.config = backend.config

    def dispatch(environ, start_response):
        service = backend_api if environ["PATH_INFO"].startswith("/api/") and environ["PATH_INFO"] != '/api/video/config' else frontend.wsgi_app
        return service(environ, start_response)

    backend.wsgi_app = dispatch
    return backend


def __getattr__(name):
    if name == 'EVENT_FIELDS':
        from backend.app import EVENT_FIELDS
        return EVENT_FIELDS
    raise AttributeError(name)


if __name__ == "__main__":
    run(create_frontend())
