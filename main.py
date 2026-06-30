#!/usr/bin/env python3
"""
Multi-Source Candidate Data Transformer — CLI Entry Point.

Usage:
    python main.py --csv data.csv --ats data.json --github user1 user2 --notes notes.txt
    python main.py --csv data.csv --config custom_config.json --output result.json
    python main.py --help

See README.md for full documentation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.models import OutputConfig
from src.pipeline import Pipeline


def setup_logging(verbose: bool = False) -> None:
    """Configure logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )


def load_config(config_path: str | None) -> OutputConfig:
    """Load output configuration from a JSON file, or return default."""
    if not config_path:
        return OutputConfig.default()

    path = Path(config_path)
    if not path.exists():
        logging.error("Config file not found: %s", config_path)
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return OutputConfig.model_validate(data)
    except Exception as e:
        logging.error("Failed to parse config %s: %s", config_path, e)
        sys.exit(1)


def load_github_usernames(
    usernames: list[str] | None,
    profiles_path: str | None,
) -> list[str] | None:
    """
    Build the list of GitHub usernames from CLI args and/or a JSON file.

    The profiles JSON file should have: {"profiles": ["user1", "user2"]}
    """
    result = []

    if usernames:
        result.extend(usernames)

    if profiles_path:
        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "profiles" in data:
                result.extend(data["profiles"])
            elif isinstance(data, list):
                result.extend(data)
        except Exception as e:
            logging.warning("Failed to load GitHub profiles from %s: %s", profiles_path, e)

    return result if result else None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="candidate-transformer",
        description=(
            "Multi-Source Candidate Data Transformer\n"
            "Ingests candidate data from CSV, ATS JSON, GitHub, and recruiter notes.\n"
            "Merges, normalizes, and outputs clean canonical profiles."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --csv recruiter.csv --ats ats.json --output profiles.json\n"
            "  python main.py --csv recruiter.csv --config custom.json --output result.json\n"
            "  python main.py --csv recruiter.csv --github torvalds --notes notes.txt\n"
        ),
    )

    # ─── Input sources ──────────────────────────────────────────
    sources = parser.add_argument_group("Input Sources")
    sources.add_argument(
        "--csv",
        metavar="PATH",
        help="Path to recruiter CSV export file",
    )
    sources.add_argument(
        "--ats",
        metavar="PATH",
        help="Path to ATS JSON blob file",
    )
    sources.add_argument(
        "--github",
        metavar="USERNAME",
        nargs="+",
        help="GitHub username(s) to fetch profiles for",
    )
    sources.add_argument(
        "--github-profiles",
        metavar="PATH",
        help='Path to JSON file with GitHub usernames: {"profiles": ["user1"]}',
    )
    sources.add_argument(
        "--github-cache",
        metavar="PATH",
        help="Path to cached GitHub API responses (for offline/deterministic use)",
    )
    sources.add_argument(
        "--notes",
        metavar="PATH",
        help="Path to recruiter notes text file",
    )

    # ─── Configuration ──────────────────────────────────────────
    config_group = parser.add_argument_group("Configuration")
    config_group.add_argument(
        "--config",
        metavar="PATH",
        help="Path to output config JSON (default: full canonical schema)",
    )

    # ─── Output ─────────────────────────────────────────────────
    output_group = parser.add_argument_group("Output")
    output_group.add_argument(
        "--output", "-o",
        metavar="PATH",
        help="Path to write JSON output (default: stdout)",
    )
    output_group.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output (default: true)",
    )
    output_group.add_argument(
        "--compact",
        action="store_true",
        help="Compact JSON output (overrides --pretty)",
    )

    # ─── Runtime ────────────────────────────────────────────────
    runtime = parser.add_argument_group("Runtime")
    runtime.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Validate: at least one source required
    if not any([args.csv, args.ats, args.github, args.github_profiles, args.notes]):
        parser.error(
            "At least one input source is required "
            "(--csv, --ats, --github, --github-profiles, or --notes)"
        )

    return args


def main() -> None:
    """Main entry point."""
    args = parse_args()
    setup_logging(verbose=args.verbose)

    logger = logging.getLogger("main")
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║   Multi-Source Candidate Data Transformer v1.0.0    ║")
    logger.info("╚══════════════════════════════════════════════════════╝")

    # Load config
    config = load_config(args.config)
    config_label = args.config if args.config else "default (full canonical)"
    logger.info("Output config: %s", config_label)

    # Build GitHub usernames list
    github_usernames = load_github_usernames(args.github, args.github_profiles)

    # Determine GitHub cache path
    github_cache = args.github_cache if hasattr(args, "github_cache") else None

    # Log sources
    logger.info("─── Input Sources ───")
    if args.csv:
        logger.info("  CSV:     %s", args.csv)
    if args.ats:
        logger.info("  ATS:     %s", args.ats)
    if github_usernames:
        logger.info("  GitHub:  %s", ", ".join(github_usernames))
    if github_cache:
        logger.info("  GH Cache: %s", github_cache)
    if args.notes:
        logger.info("  Notes:   %s", args.notes)
    logger.info("─────────────────────")

    # Run pipeline
    pipeline = Pipeline(github_cache_path=github_cache)
    result = pipeline.run(
        csv_path=args.csv,
        ats_path=args.ats,
        github_usernames=github_usernames,
        notes_path=args.notes,
        config=config,
    )

    # Log results
    logger.info("─── Results ───")
    logger.info("  Profiles:  %d", len(result.profiles))
    logger.info("  Warnings:  %d", len(result.warnings))
    logger.info("  Errors:    %d", len(result.errors))

    for status in result.source_statuses:
        logger.info(
            "  Source %-15s │ %-10s │ %s",
            status.source_type.value,
            status.status.value,
            status.error_message or "OK",
        )

    if result.warnings:
        logger.info("─── Warnings ───")
        for w in result.warnings:
            logger.warning("  %s", w)

    if result.errors:
        logger.info("─── Errors ───")
        for e in result.errors:
            logger.error("  %s", e)

    # Format output
    indent = None if args.compact else 2
    output_data = {
        "metadata": {
            "total_profiles": len(result.profiles),
            "sources_processed": [
                {
                    "type": s.source_type.value,
                    "path": s.path,
                    "status": s.status.value,
                }
                for s in result.source_statuses
            ],
            "warnings": result.warnings,
            "errors": result.errors,
        },
        "profiles": result.profiles,
    }

    output_json = json.dumps(output_data, indent=indent, ensure_ascii=False, default=str)

    # Write output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_json, encoding="utf-8")
        logger.info("Output written to: %s", args.output)
    else:
        print(output_json)

    logger.info("Done.")


if __name__ == "__main__":
    main()
