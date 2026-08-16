#!/usr/bin/env python3
"""Validate and normalize a Nixpacks Dockerfile context for remote ACR build."""
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path


EXPECTED_BUILD_ARGS = {
    "NIXPACKS_METADATA": "python",
    "NIXPACKS_PYTHON_VERSION": "3.13",
    "PIP_DEFAULT_TIMEOUT": "100",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_CACHE_DIR": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONFAULTHANDLER": "1",
    "PYTHONHASHSEED": "random",
    "PYTHONUNBUFFERED": "1",
}


def _parse_build_script(
    source: Path,
    script: Path,
    expected_image: str,
    revision: str,
) -> None:
    tokens = shlex.split(script.read_text())
    if len(tokens) < 3 or tokens[:2] != ["docker", "build"]:
        raise ValueError("unexpected Nixpacks build command")
    if Path(tokens[2]).resolve() != source.resolve():
        raise ValueError("Nixpacks build context is not the archived source")

    build_args: dict[str, str] = {}
    labels: list[str] = []
    dockerfile = None
    image = None
    platform = None
    index = 3
    while index < len(tokens):
        option = tokens[index]
        if option not in {"-f", "-t", "--build-arg", "--label", "--platform"}:
            raise ValueError(f"unexpected Nixpacks build option: {option}")
        if index + 1 >= len(tokens):
            raise ValueError("Nixpacks build option has no value")
        value = tokens[index + 1]
        index += 2
        if option == "-f":
            dockerfile = value
        elif option == "-t":
            image = value
        elif option == "--build-arg":
            name, separator, argument = value.partition("=")
            if not separator or name in build_args:
                raise ValueError("invalid or duplicate Nixpacks build argument")
            build_args[name] = argument
        elif option == "--label":
            labels.append(value)
        else:
            platform = value

    expected_dockerfile = source / ".nixpacks" / "Dockerfile"
    if dockerfile is None or Path(dockerfile).resolve() != expected_dockerfile.resolve():
        raise ValueError("Nixpacks selected an unexpected Dockerfile")
    if image != expected_image or platform != "linux/amd64":
        raise ValueError("Nixpacks image name or platform drifted")
    if labels != [f"org.opencontainers.image.revision={revision}"]:
        raise ValueError("Nixpacks revision label drifted")
    if build_args != EXPECTED_BUILD_ARGS:
        raise ValueError("Nixpacks build arguments drifted")


def finalize(source: Path, expected_image: str, revision: str, epoch: int) -> None:
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError("revision must be a full lowercase Git object ID")
    nixpacks = source / ".nixpacks"
    dockerfile = nixpacks / "Dockerfile"
    build_script = nixpacks / "build.sh"
    if not dockerfile.is_file() or not build_script.is_file():
        raise ValueError("Nixpacks output is incomplete")
    _parse_build_script(source, build_script, expected_image, revision)

    content = dockerfile.read_text()
    locked_install = (
        "python -m pip install --require-hashes --only-binary=:all: "
        "-r requirements.lock"
    )
    expected_cache = f"id={revision}-/root/cache/pip"
    if (
        locked_install not in content
        or "requirements.txt" in content
        or expected_cache not in content
    ):
        raise ValueError("generated Dockerfile does not install only the hash lock")
    content = content.rstrip() + (
        f'\n\nLABEL org.opencontainers.image.revision="{revision}"\n'
    )
    dockerfile.write_text(content)

    # build.sh embeds a random tag and absolute temporary paths. It is not used
    # by ACR and must not enter COPY layers or make the uploaded context vary.
    build_script.unlink()
    # Azure CLI tars the whole context, including directory metadata. Normalize
    # every archived/generated entry (and the context root) so operator clock
    # and temporary-directory creation time cannot perturb COPY layer digests.
    for path in sorted(source.rglob("*"), reverse=True) + [source]:
        os.utime(path, (epoch, epoch), follow_symlinks=False)


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: finalize-nixpacks-context.py <source> <image:tag> <revision> <epoch>",
            file=sys.stderr,
        )
        return 2
    try:
        finalize(Path(argv[1]), argv[2], argv[3], int(argv[4]))
    except (OSError, ValueError) as error:
        print(f"invalid Nixpacks context: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
