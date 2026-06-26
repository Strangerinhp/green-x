from __future__ import annotations

from threading import Thread
from typing import Any

from flask import Flask, jsonify, render_template
from werkzeug.serving import make_server

from .state_store import LiveStateStore


class DashboardServer:
    def __init__(self, store: LiveStateStore, host: str = "127.0.0.1", port: int = 8050) -> None:
        self._app = create_app(store)
        self._server = make_server(host, port, self._app)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)


def create_app(store: LiveStateStore) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    @app.get("/")
    def index() -> str:
        state = store.snapshot()
        return render_template("index.html", initial_state=state)

    @app.get("/api/state")
    def state() -> Any:
        return jsonify(store.snapshot())

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"ok": True})

    return app

