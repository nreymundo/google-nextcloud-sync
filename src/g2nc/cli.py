from __future__ import annotations

import argparse
import logging
from pathlib import Path

from g2nc.config import ConfigError, load_config
from g2nc.google.client import GoogleCalendarClient
from g2nc.google.oauth import bootstrap_token
from g2nc.locking import FileLock
from g2nc.logging_utils import configure_logging
from g2nc.nextcloud.client import NextcloudCalendarClient
from g2nc.state import SqliteStateRepository
from g2nc.sync_service import SyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="g2nc")
    parser.add_argument("--config", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate-config")

    auth_parser = subparsers.add_parser("auth")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_bootstrap = auth_subparsers.add_parser("bootstrap")
    auth_bootstrap.add_argument("--no-browser", action="store_true")

    subparsers.add_parser("sync")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    configure_logging(config.logging.level, config.logging.json)
    logger = logging.getLogger(__name__)

    if args.command == "validate-config":
        logger.info("configuration valid", extra={"mappings": len(config.mappings)})
        return 0

    if args.command == "auth" and args.auth_command == "bootstrap":
        bootstrap_token(config.google, open_browser=not args.no_browser)
        logger.info("oauth bootstrap complete", extra={"token_file": str(config.google.token_file)})
        return 0

    if args.command == "sync":
        state = SqliteStateRepository(config.sqlite_path)
        state.initialize()

        google = GoogleCalendarClient(config.google)
        nextcloud = NextcloudCalendarClient(config.nextcloud)
        service = SyncService(google=google, nextcloud=nextcloud, state=state)

        with FileLock(config.lock_file):
            for mapping in config.mappings:
                service.sync_mapping(mapping)
        return 0

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
