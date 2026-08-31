"""Local-only entry point for the bundled Kilix Voicebox server."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from backend import config, database
from backend.main import app, configure_session_token


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
LOGGER = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Kilix Voicebox local backend")
    parser.add_argument("--port", type=int, default=17_493)
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be within 1..65535")
    if args.data_dir:
        config.set_data_dir(args.data_dir)
    token = os.environ.get("KILIX_VOICEBOX_SESSION_TOKEN")
    if token is None:
        parser.error("KILIX_VOICEBOX_SESSION_TOKEN is required")
    configure_session_token(token)
    database.init_db()
    LOGGER.info("Starting local Voicebox product service on loopback port %d", args.port)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
