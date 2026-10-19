#!/usr/bin/env python3
"""Prepare relocatable Nginx binaries as an ordinary user, never as root."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess


def dependencies(path):
    lines = subprocess.check_output(["/usr/bin/otool", "-L", str(path)], text=True)
    return [line.strip().split(" (", 1)[0] for line in lines.splitlines()[1:]]


def system_library(path):
    return path.startswith(("/usr/lib/", "/System/Library/"))


def build(source, destination):
    if os.geteuid() == 0:
        raise PermissionError("Build the bundle as an ordinary user")
    destination.mkdir(mode=0o700)
    (destination / "bin").mkdir()
    (destination / "lib").mkdir()
    libraries = {}
    pending = [source.resolve()]
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for dependency in dependencies(path):
            if system_library(dependency):
                continue
            if not dependency.startswith("/opt/homebrew/"):
                raise ValueError("Unexpected non-system dependency: " + dependency)
            resolved = Path(dependency).resolve()
            # A dylib's install ID also appears in otool -L.
            if resolved == path:
                continue
            name = Path(dependency).name
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.dylib", name):
                raise ValueError("Unsafe library name")
            if name in libraries and libraries[name] != resolved:
                raise ValueError("Library basename collision")
            libraries[name] = resolved
            pending.append(resolved)
    outputs = [(source.resolve(), destination / "bin/nginx")]
    outputs.extend((original, destination / "lib" / name)
                   for name, original in libraries.items())
    for original, target in outputs:
        shutil.copyfile(original, target)
        target.chmod(0o755)
        is_binary = target.name == "nginx"
        if not is_binary:
            subprocess.run(["/usr/bin/install_name_tool", "-id",
                            "@loader_path/" + target.name, str(target)], check=True)
        for dependency in dependencies(original):
            if system_library(dependency) or Path(dependency).resolve() == original:
                continue
            prefix = "@executable_path/../lib/" if is_binary else "@loader_path/"
            subprocess.run(["/usr/bin/install_name_tool", "-change", dependency,
                            prefix + Path(dependency).name, str(target)], check=True)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(target)], check=True)
    subprocess.run([str(destination / "bin/nginx"), "-v"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path("/opt/homebrew/bin/nginx"))
    args = parser.parse_args()
    build(args.source, args.destination)
