#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
POLICY_PATH = Path("/etc/awg-bot-helper.json")


def _safe_name(value: str, field: str) -> str:
    if not value or not SAFE_NAME_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _safe_public_key(value: str) -> str:
    if not value:
        raise ValueError("invalid public key")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as e:
        raise ValueError("invalid public key") from e
    if len(raw) != 32:
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


def _run_nft_script(script: str) -> str:
    return _run(["nft", "-f", "-"], stdin_text=script)


def _load_policy(path: Path | None = None) -> tuple[str, str]:
    policy_path = path or POLICY_PATH
    try:
        st = policy_path.lstat()
    except FileNotFoundError as e:
        raise RuntimeError(f"helper policy file not found: {policy_path}") from e
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError("helper policy must not be a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError("helper policy must be a regular file")
    if st.st_uid != 0:
        raise RuntimeError("helper policy must be owned by root")
    if st.st_mode & 0o022:
        raise RuntimeError("helper policy is writable by group/others")

    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"invalid helper policy json: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError("invalid helper policy: expected object")
    container = _safe_name(str(data.get("container", "")).strip(), "policy container")
    interface = _safe_name(str(data.get("interface", "")).strip(), "policy interface")
    return container, interface


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restricted AWG helper")
    sub = parser.add_subparsers(dest="op", required=True)

    for op_name in ("check-awg", "show", "genkey", "pubkey", "genpsk", "qos-check", "qos-sync", "denylist-check"):
        sub.add_parser(op_name)

    p_add = sub.add_parser("add-peer")
    p_add.add_argument("--public-key", required=True)
    p_add.add_argument("--ip", required=True)
    p_add.add_argument("--psk")

    p_del = sub.add_parser("remove-peer")
    p_del.add_argument("--public-key", required=True)
    p_qos_set = sub.add_parser("qos-set")
    p_qos_set.add_argument("--ip", required=True)
    p_qos_set.add_argument("--rate-mbit", required=True)
    p_qos_clear = sub.add_parser("qos-clear")
    p_qos_clear.add_argument("--ip", required=True)
    p_denylist_sync = sub.add_parser("denylist-sync")
    p_denylist_sync.add_argument("--vpn-subnet", required=True)
    p_denylist_clear = sub.add_parser("denylist-clear")
    p_denylist_clear.add_argument("--vpn-subnet", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        container, interface = _load_policy()

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
        if args.op == "qos-check":
            print(_run(["tc", "qdisc", "show", "dev", interface]))
            return 0
        if args.op == "qos-set":
            ip = _safe_ipv4(args.ip.strip())
            rate_mbit = int(str(args.rate_mbit).strip())
            if rate_mbit <= 0 or rate_mbit > 10000:
                raise RuntimeError("invalid rate-mbit")
            classid_suffix = ip.split(".")[-1]
            _run(["tc", "qdisc", "replace", "dev", interface, "root", "handle", "1:", "htb", "default", "9999"])
            _run(["tc", "class", "replace", "dev", interface, "parent", "1:", "classid", f"1:{classid_suffix}", "htb", "rate", f"{rate_mbit}mbit", "ceil", f"{rate_mbit}mbit"])
            _run(["tc", "filter", "replace", "dev", interface, "protocol", "ip", "parent", "1:0", "prio", "1", "u32", "match", "ip", "dst", f"{ip}/32", "flowid", f"1:{classid_suffix}"])
            print(f"qos set {ip} {rate_mbit}mbit")
            return 0
        if args.op == "qos-clear":
            ip = _safe_ipv4(args.ip.strip())
            classid_suffix = ip.split(".")[-1]
            _run(["tc", "filter", "delete", "dev", interface, "protocol", "ip", "parent", "1:0", "prio", "1", "u32", "match", "ip", "dst", f"{ip}/32"], stdin_text=None)
            _run(["tc", "class", "delete", "dev", interface, "classid", f"1:{classid_suffix}"])
            print(f"qos clear {ip}")
            return 0
        if args.op == "qos-sync":
            payload = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
            _run(["tc", "qdisc", "replace", "dev", interface, "root", "handle", "1:", "htb", "default", "9999"])
            for line in payload:
                ip_raw, rate_raw = line.split(",", 1)
                ip = _safe_ipv4(ip_raw.strip())
                rate_mbit = int(rate_raw.strip())
                classid_suffix = ip.split(".")[-1]
                _run(["tc", "class", "replace", "dev", interface, "parent", "1:", "classid", f"1:{classid_suffix}", "htb", "rate", f"{rate_mbit}mbit", "ceil", f"{rate_mbit}mbit"])
                _run(["tc", "filter", "replace", "dev", interface, "protocol", "ip", "parent", "1:0", "prio", "1", "u32", "match", "ip", "dst", f"{ip}/32", "flowid", f"1:{classid_suffix}"])
            print(f"qos synced {len(payload)}")
            return 0
        if args.op == "denylist-check":
            print(_run(["nft", "list", "table", "inet", "filter"]))
            return 0
        if args.op == "denylist-clear":
            ipaddress.ip_network(args.vpn_subnet.strip(), strict=False)
            script = (
                "add table inet filter\n"
                "add chain inet filter awg_forward { type filter hook forward priority 0; policy accept; }\n"
                "add set inet filter awg_denylist { type ipv4_addr; flags interval; }\n"
                "flush chain inet filter awg_forward\n"
                "flush set inet filter awg_denylist\n"
            )
            _run_nft_script(script)
            print("denylist cleared")
            return 0
        if args.op == "denylist-sync":
            subnet = ipaddress.ip_network(args.vpn_subnet.strip(), strict=False)
            cidrs = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
            validated = [str(ipaddress.ip_network(c, strict=False)) for c in cidrs]
            elements = ", ".join(validated) if validated else ""
            script = (
                "add table inet filter\n"
                "add chain inet filter awg_forward { type filter hook forward priority 0; policy accept; }\n"
                "add set inet filter awg_denylist { type ipv4_addr; flags interval; }\n"
                "flush chain inet filter awg_forward\n"
                f"flush set inet filter awg_denylist\n"
            )
            if elements:
                script += f"add element inet filter awg_denylist {{ {elements} }}\n"
            script += f"add rule inet filter awg_forward ip saddr {subnet} ip daddr @awg_denylist drop comment \"awg_denylist\"\n"
            _run_nft_script(script)
            print(f"denylist synced {len(cidrs)}")
            return 0
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
