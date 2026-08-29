#!/usr/bin/env python3
"""Validate the immutable metadata carried by deployment authorization tags."""

import argparse
import datetime as dt
import re
import sys


VERSION_RE = re.compile(
    r"^(?P<year>20\d{2})\.(?P<month>[1-9]|1[0-2])\.(?P<day>[1-9]|[12]\d|3[01])"
    r"(?:\.(?P<time>\d{6})\.dev0)?$"
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(
    r"^deploy-ytdlp-v(?P<version>[0-9][0-9.dev]+)--(?P<commit>[0-9a-f]{40})$"
)


class ReleaseMetadataError(ValueError):
    """Release metadata is malformed or does not match the checked-out code."""


def validate_version(version: str) -> str:
    """Return a strict, normalized yt-dlp distribution version."""
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ReleaseMetadataError(f"invalid yt-dlp distribution version: {version!r}")

    try:
        dt.date(*(int(match.group(part)) for part in ("year", "month", "day")))
    except ValueError as error:
        raise ReleaseMetadataError(f"invalid yt-dlp release date: {version!r}") from error

    time_value = match.group("time")
    if time_value:
        hours, minutes, seconds = map(
            int,
            (time_value[0:2], time_value[2:4], time_value[4:6]),
        )
        if hours > 23 or minutes > 59 or seconds > 59:
            raise ReleaseMetadataError(f"invalid yt-dlp nightly time: {version!r}")

    return version


def validate_commit(commit: str) -> str:
    """Return a full lowercase Git object ID, rejecting abbreviations."""
    if not COMMIT_RE.fullmatch(commit):
        raise ReleaseMetadataError(f"invalid full Git commit: {commit!r}")
    return commit


def build_release_tag(version: str, commit: str) -> str:
    """Build the lightweight tag used as the deployment authorization token."""
    return f"deploy-ytdlp-v{validate_version(version)}--{validate_commit(commit)}"


def verify_release_tag(tag: str, commit: str) -> str:
    """Validate *tag* and return its exact PEP 440 yt-dlp version."""
    match = TAG_RE.fullmatch(tag)
    if not match:
        raise ReleaseMetadataError(f"invalid deployment tag: {tag!r}")

    version = validate_version(match.group("version"))
    expected_commit = validate_commit(commit)
    if match.group("commit") != expected_commit:
        raise ReleaseMetadataError(
            "deployment tag commit does not match checkout: "
            f"{match.group('commit')} != {expected_commit}"
        )
    return version


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-version")
    validate.add_argument("version")

    tag = commands.add_parser("tag")
    tag.add_argument("version")
    tag.add_argument("commit")

    verify = commands.add_parser("verify-tag")
    verify.add_argument("tag")
    verify.add_argument("commit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "validate-version":
            print(validate_version(args.version))
        elif args.command == "tag":
            print(build_release_tag(args.version, args.commit))
        else:
            print(verify_release_tag(args.tag, args.commit))
    except ReleaseMetadataError as error:
        print(f"release metadata error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
