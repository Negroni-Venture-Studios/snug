"""The `snug` command: Django's management commands, with clear config errors.

    snug [--env-file PATH] <command> [options]

`--env-file` loads settings from a file without overriding variables that are
already set, as Docker Compose does. Useful commands:

    snug version      the version of Snug running, as the Overview shows it
    snug serve        apply migrations, then serve with gunicorn
    snug migrate      apply migrations only
    snug check        check the configuration and the project
"""

import os
import sys
from pathlib import Path

from snug.config import ConfigError, read_env_file
from snug.core.hardening import make_undumpable


def main(argv: list[str] | None = None) -> int:
    # Before reading any secret: keep this process's memory and environment
    # out of reach of other processes running as the same user.
    make_undumpable()
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["--env-file"]:
            if len(args) < 2:
                print("snug: --env-file needs a path.", file=sys.stderr)
                return 2
            for key, value in read_env_file(Path(args[1])).items():
                os.environ.setdefault(key, value)
            args = args[2:]

        # Snug's version, not Django's: Django answers `version` before it
        # looks for a command of that name. It needs no settings.
        if args in (["version"], ["--version"]):
            from snug.core.version import running

            print(running().name)
            return 0

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snug.settings")
        import django

        django.setup()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except OSError as error:
        print(f"snug: cannot read the environment file: {error.strerror}.", file=sys.stderr)
        return 2

    # Every command may touch the database, so make sure its folder exists,
    # readable by Snug alone. On a fresh server this is the first run.
    from django.conf import settings

    data_dir = settings.CONFIG.data_dir
    try:
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in ("originals", "renders", "tmp"):
            (data_dir / folder).mkdir(mode=0o700, exist_ok=True)
    except OSError as error:
        print(f"snug: cannot create the data folder {data_dir}: {error.strerror}.", file=sys.stderr)
        return 2

    from django.core.management import execute_from_command_line

    execute_from_command_line(["snug", *args])
    return 0


if __name__ == "__main__":
    sys.exit(main())
