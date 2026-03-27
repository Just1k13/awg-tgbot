#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys

SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
SAFE_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/=]{40,64}$")
POLICY_PATH = os.getenv("AWG_HELPER_POLICY_PATH", "/etc/awg-bot-helper.json")


def _safe_name(value: str, field: str) -> str:
    if not value or not SAFE_NAME_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _safe_public_key(value: str) -> str:
    if not value or not SAFE_PUBKEY_RE.fullmatch(value):
        raise ValueError("invalid public key")
    return value


def _safe_ipv4(value: str) -> str:
    ip = ipaddress.ip_address(value)
    if ip.version != 4:
        raise ValueError("invalid ip")
    return str(ip)


def _run(args: list[str], stdin_text: str | None = None) -> str:
    proc = subprocess.run(
        args,
        input=stdin_text.encode("utf-8") if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", errors="ignore").strip() or proc.stdout.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(msg or "helper command failed")
    return proc.stdout.decode("utf-8", errors="ignore").strip()


def _docker_exec(container: str, cmd: list[str], stdin_text: str | None = None) -> str:
    return _run(["docker", "exec", "-i", container, *cmd], stdin_text=stdin_text)


def _load_policy() -> dict[str, str]:
    try:
        st = os.lstat(POLICY_PATH)
    except FileNotFoundError as e:
        raise RuntimeError(f"helper policy file not found: {POLICY_PATH}") from e
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError("helper policy must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError("helper policy must be a regular file")
    if st.st_uid != 0:
        raise RuntimeError("helper policy must be owned by root")
    if st.st_mode & 0o022:
        raise RuntimeError("helper policy is writable by group/others")

    try:
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"invalid helper policy json: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError("invalid helper policy: expected object")
    container = _safe_name(str(data.get("container", "")).strip(), "policy container")
    interface = _safe_name(str(data.get("interface", "")).strip(), "policy interface")
    return {"container": container, "interface": interface}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restricted AWG helper")
    sub = parser.add_subparsers(dest="op", required=True)

    for op_name in ("check-awg", "show", "genkey", "pubkey", "genpsk"):
        p = sub.add_parser(op_name)
        p.add_argument("--container", required=True)
        if op_name in ("check-awg", "show"):
            p.add_argument("--interface", required=True)

    p_add = sub.add_parser("add-peer")
    p_add.add_argument("--container", required=True)
    p_add.add_argument("--interface", required=True)
    p_add.add_argument("--public-key", required=True)
    p_add.add_argument("--ip", required=True)
    p_add.add_argument("--psk")

    p_del = sub.add_parser("remove-peer")
    p_del.add_argument("--container", required=True)
    p_del.add_argument("--interface", required=True)
    p_del.add_argument("--public-key", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        policy = _load_policy()
        container = _safe_name(args.container, "container")
        if container != policy["container"]:
            raise RuntimeError("container denied by policy")
        if args.op in ("check-awg", "show", "add-peer", "remove-peer"):
            interface = _safe_name(args.interface, "interface")
            if interface != policy["interface"]:
                raise RuntimeError("interface denied by policy")

        if args.op == "check-awg":
            out = _docker_exec(container, ["awg", "show", interface])
            if "interface:" not in out:
                raise RuntimeError("awg interface not available")
            print(out)
            return 0
        if args.op == "show":
            print(_docker_exec(container, ["awg", "show", interface]))
            return 0
        if args.op == "genkey":
            print(_docker_exec(container, ["awg", "genkey"]))
            return 0
        if args.op == "pubkey":
            private_key = sys.stdin.read().strip()
            if not private_key:
                raise RuntimeError("empty private key")
            print(_docker_exec(container, ["awg", "pubkey"], stdin_text=private_key))
            return 0
        if args.op == "genpsk":
            print(_docker_exec(container, ["wg", "genpsk"]))
            return 0
        if args.op == "add-peer":
            public_key = _safe_public_key(args.public_key.strip())
            ip = _safe_ipv4(args.ip.strip())
            psk_raw = args.psk if args.psk is not None else sys.stdin.read()
            psk = psk_raw.strip()
            if not psk:
                raise RuntimeError("empty psk")
            print(
                _docker_exec(
                    container,
                    ["awg", "set", interface, "peer", public_key, "preshared-key", "/dev/stdin", "allowed-ips", f"{ip}/32"],
                    stdin_text=psk,
                )
            )
            return 0
        if args.op == "remove-peer":
            public_key = _safe_public_key(args.public_key.strip())
            print(_docker_exec(container, ["awg", "set", interface, "peer", public_key, "remove"]))
            return 0
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
