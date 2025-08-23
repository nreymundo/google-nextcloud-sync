"""CLI entrypoint for g2nc.

Commands
- sync:    main one-way incremental sync (Google -> Nextcloud)
- status:  show tokens, counts, last run (stub for now)
- prune:   clean orphaned mappings with safety checks (stub for now)

Notes
- Configuration precedence: CLI > ENV (G2NC__) > YAML file, see config loader.
- When env G2NC_DEV_SCAFFOLD=1 is set, `sync` prints scaffold info and exits 0 (useful for unit tests).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer

from .config import AppConfig, load_config

# Logging
try:  # pragma: no cover - exercised once logging module lands
    from .logging import setup_logging  # type: ignore
except Exception:  # pragma: no cover
    import logging

    def setup_logging(level: str = "INFO", json: bool = False) -> None:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))


# Orchestrator
try:
    from .sync.orchestrator import Orchestrator  # type: ignore
except Exception:  # pragma: no cover
    Orchestrator = None  # type: ignore[assignment]


app = typer.Typer(add_completion=False, help="Google → Nextcloud one-way incremental sync tool")


def _parse_calendar_map(spec: str | None) -> dict[str, str]:
    """Parse CLI calendar map spec: key1:val1,key2:val2 -> {key1: val1, key2: val2}"""
    mapping: dict[str, str] = {}
    if not spec:
        return mapping
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    for p in parts:
        if ":" not in p:
            raise typer.BadParameter(f"Invalid calendar-map entry '{p}'. Expected key:value")
        k, v = p.split(":", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            raise typer.BadParameter(f"Invalid calendar-map entry '{p}'. Empty key or value")
        mapping[k] = v
    return mapping


def _cli_overrides_from_args(
    *,
    photo_sync: bool | None,
    protect_local: bool | None,
    time_window_days: int | None,
    dry_run: bool | None,
    calendar_map: str | None,
    verbose: bool,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}

    # sync settings
    sync_over: dict[str, Any] = {}
    if photo_sync is not None:
        sync_over["photo_sync"] = photo_sync
    if protect_local is not None:
        sync_over["protect_local"] = protect_local
        if protect_local:
            sync_over["overwrite_local"] = False
    if time_window_days is not None:
        sync_over["time_window_days"] = time_window_days
    if dry_run is not None:
        sync_over["dry_run"] = dry_run
    if sync_over:
        overrides["sync"] = sync_over

    # google calendar id mapping from CLI
    cal_map = _parse_calendar_map(calendar_map)
    if cal_map:
        overrides.setdefault("google", {})["calendar_ids"] = cal_map

    # logging
    if verbose:
        overrides.setdefault("logging", {})["level"] = "DEBUG"

    return overrides


@app.command(help="Run one-way incremental sync (Google → Nextcloud).")
def sync(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=False,
        readable=True,
        help="Path to YAML config file.",
    ),
    contacts: bool = typer.Option(
        False,
        "--contacts",
        help="Include contacts sync.",
        show_default=False,
    ),
    calendar: bool = typer.Option(
        False,
        "--calendar",
        help="Include calendar sync.",
        show_default=False,
    ),
    calendar_map: str | None = typer.Option(
        None,
        "--calendar-map",
        help="Comma-separated key:googleCalendarId pairs. Example: default:primary,team:team@group.calendar.google.com",
        show_default=False,
    ),
    photo_sync: bool | None = typer.Option(
        None,
        "--photo-sync/--no-photo-sync",
        help="Enable or disable contact photo syncing (overrides config).",
        show_default=False,
    ),
    dry_run: bool | None = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Do not write changes to Nextcloud; log intended actions.",
        show_default=False,
    ),
    reset_tokens: bool = typer.Option(
        False,
        "--reset-tokens",
        help="Reset stored sync tokens (forces a fresh/full sync window).",
        show_default=False,
    ),
    protect_local: bool | None = typer.Option(
        None,
        "--protect-local/--no-protect-local",
        help="Protect local Nextcloud changes from being overwritten.",
        show_default=False,
    ),
    time_window_days: int | None = typer.Option(
        None,
        "--time-window-days",
        min=1,
        help="Bounded resync window used when tokens are invalid.",
        show_default=False,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Set log level to DEBUG (overrides config.logging.level).",
    ),
) -> None:
    """Sync command."""
    overrides = _cli_overrides_from_args(
        photo_sync=photo_sync,
        protect_local=protect_local,
        time_window_days=time_window_days,
        dry_run=dry_run,
        calendar_map=calendar_map,
        verbose=verbose,
    )
    cfg: AppConfig = load_config(file_path=str(config) if config else None, cli_overrides=overrides)

    # init logging
    try:
        from .logging import setup_logging as _setup  # local import to avoid early import cycles
    except Exception:

        def _setup(level: str = "INFO", json: bool = False) -> None:  # fallback
            import logging

            logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))

    # LoggingConfig now exposes `as_json` (alias "json")
    setup_logging(level=cfg.logging.level, json=getattr(cfg.logging, "as_json", False))

    # Resolve what to run
    run_contacts = contacts
    run_calendar = calendar
    if not run_contacts and not run_calendar:
        # default to both when neither flag provided
        run_contacts = run_calendar = True

    # Unit-test/dev scaffold path (no orchestrator execution)
    if os.getenv("G2NC_DEV_SCAFFOLD", "").lower() in {"1", "true", "yes"}:
        typer.echo("g2nc sync scaffold")
        typer.echo(f"  contacts: {run_contacts} | calendar: {run_calendar}")
        if calendar_map:
            typer.echo(f"  calendar_map: {_parse_calendar_map(calendar_map)}")
        typer.echo(
            f"  dry_run: {cfg.sync.dry_run} | photo_sync: {cfg.sync.photo_sync} | protect_local: {cfg.sync.protect_local}"
        )
        typer.echo(f"  config file: {config or '(none)'}")
        if reset_tokens:
            typer.echo("  NOTE: --reset-tokens requested")
        raise typer.Exit(code=0)

    # Orchestrated run
    if Orchestrator is None:  # pragma: no cover
        typer.echo("Orchestrator unavailable; rebuild environment", err=True)
        raise typer.Exit(code=3)

    orch = Orchestrator(cfg)
    exit_code, summary = orch.run(
        do_contacts=run_contacts,
        do_calendar=run_calendar,
        reset_tokens=reset_tokens,
    )
    agg = summary.aggregate()
    typer.echo(
        "g2nc sync summary: "
        f"fetched={agg['fetched']} created={agg['created']} updated={agg['updated']} "
        f"deleted={agg['deleted']} skipped={agg['skipped']} errors={agg['errors']}"
    )
    raise typer.Exit(code=exit_code)


@app.command(help="Show status, tokens, counts, last run (stub).")
def status(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to YAML config file.", show_default=False
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Set log level to DEBUG (overrides config.logging.level)."
    ),
) -> None:
    cfg: AppConfig = load_config(
        file_path=str(config) if config else None,
        cli_overrides={"logging": {"level": "DEBUG"} if verbose else {}},
    )
    setup_logging(level=cfg.logging.level, json=getattr(cfg.logging, "as_json", False))
    typer.echo("g2nc status scaffold (state DAO pending)")
    raise typer.Exit(code=0)


@app.command(help="Prune orphaned mappings with safety checks (stub).")
def prune(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to YAML config file.", show_default=False
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Proceed without interactive confirmation."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Set log level to DEBUG (overrides config.logging.level)."
    ),
) -> None:
    cfg: AppConfig = load_config(
        file_path=str(config) if config else None,
        cli_overrides={"logging": {"level": "DEBUG"} if verbose else {}},
    )
    setup_logging(level=cfg.logging.level, json=getattr(cfg.logging, "as_json", False))
    if not yes:
        typer.echo("Dry-run prune. Use --yes to proceed (implementation pending).")
    else:
        typer.echo("Prune requested (implementation pending).")
    raise typer.Exit(code=0)


if __name__ == "__main__":  # pragma: no cover
    app()
