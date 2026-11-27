import os

from .app import create_app


def run(app):
    app.run(host=os.environ.get("FLASK_HOST", "0.0.0.0"),
            port=int(os.environ.get("FLASK_PORT", "5000")), debug=False)


if __name__ == "__main__":
    run(create_app())
