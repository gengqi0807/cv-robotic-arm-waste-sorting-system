import os

from .app import create_app


def main():
    app = create_app()
    app.run(host=os.environ.get("BACKEND_HOST", "127.0.0.1"),
            port=int(os.environ.get("BACKEND_PORT", "5001")), debug=False)


if __name__ == "__main__":
    main()
