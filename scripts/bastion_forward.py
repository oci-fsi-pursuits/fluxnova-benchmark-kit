#!/usr/bin/env python3
"""Forward localhost:2222 to the deployed controller through OCI Bastion.

Requires Python 3.12+, the OCI SDK (pip install oci==2.187.1), and OpenSSH.
Reads Terraform's applied environment_contract, or --contract JSON exported
from Resource Manager. It never applies Terraform or modifies network rules.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT = 120
STARTUP_ATTEMPTS = 6
STARTUP_RETRY_DELAY = 10


def load_contract(path: Path | None, terraform_dir: Path) -> dict:
    if path:
        text = path.read_text(encoding="utf-8-sig")
    else:
        terraform = shutil.which("terraform")
        if not terraform:
            raise ValueError("Terraform is not on PATH. Supply --contract with exported environment_contract JSON.")
        result = subprocess.run(
            [terraform, f"-chdir={terraform_dir.resolve()}", "output", "-json", "environment_contract"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise ValueError("No applied environment_contract is available. Apply the reviewed Bastion plan first, "
                             "or use --contract for a Resource Manager deployment.\n" + result.stderr.strip())
        text = result.stdout
    contract = json.loads(text)
    if isinstance(contract, dict) and "environment_contract" in contract:
        contract = contract["environment_contract"]["value"]
    if not isinstance(contract, dict):
        raise ValueError("Expected an environment_contract JSON object.")
    return contract


def connection_details(contract: dict) -> tuple[str, str, str, str]:
    bastion = contract.get("bastion") or {}
    bastion_id = bastion.get("id", "")
    if not re.fullmatch(r"ocid1\.bastion\.[A-Za-z0-9._-]+", bastion_id):
        raise ValueError("No deployed Bastion ID. Set bastion_client_cidrs and apply the reviewed plan first.")
    region = contract.get("region", "")
    if not re.fullmatch(r"[a-z][a-z0-9-]+", region):
        raise ValueError("Invalid region in environment contract.")
    controller = contract.get("roles", {}).get("controller", {})
    address = ipaddress.ip_address(controller.get("private_ip", ""))
    private_ranges = [ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if address.version != 4 or not any(address in network for network in private_ranges):
        raise ValueError("Controller target must be an RFC1918 private IPv4 address.")
    username = controller.get("user", "ubuntu")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", username):
        raise ValueError("Invalid controller SSH username.")
    return bastion_id, region, str(address), username


def bastion_destination(session) -> str:
    # Read only the endpoint from OCI's command; never execute a command string.
    command = (session.ssh_metadata or {}).get("command", "")
    matches = re.findall(r"(ocid1\.bastionsession\.[A-Za-z0-9._-]+)@([A-Za-z0-9][A-Za-z0-9.-]*)", command)
    if len(matches) != 1 or matches[0][0] != session.id:
        raise ValueError("OCI did not return a valid SSH endpoint for this Bastion session.")
    return "@".join(matches[0])


def tunnel_command(ssh: str, key: Path, local_port: int, target_ip: str, session) -> list[str]:
    return [
        ssh, "-i", str(key), "-N", "-T",
        "-o", "IdentitiesOnly=yes", "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=20",
        "-L", f"127.0.0.1:{local_port}:{target_ip}:22",
        "-p", "22", bastion_destination(session),
    ]


def wait_active(client, session_id: str, timeout: int = 300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = client.get_session(session_id).data
        if session.lifecycle_state == "ACTIVE":
            return session
        if session.lifecycle_state in {"FAILED", "DELETING", "DELETED"}:
            raise RuntimeError(f"Bastion session entered {session.lifecycle_state}.")
        time.sleep(3)
    raise TimeoutError("Timed out waiting for the Bastion session to become ACTIVE.")


def display_command(command: list[str]) -> str:
    if os.name == "nt":
        # Paste-safe PowerShell quoting; no shell is used to launch the tunnel.
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in command)
    return shlex.join(command)


def wait_for_forward(process, port: int, deadline: float) -> bool:
    """Confirm the target SSH banner, not just an open local listener."""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as probe:
                if probe.recv(255).startswith(b"SSH-") and process.poll() is None:
                    return True
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError("Controller SSH did not respond through Bastion within 120 seconds. "
                       "Check the SSH output, client IP allowlist and controller reachability.")


def run_session(client, details, ssh: str, key: Path, port: int, target_ip: str, username: str) -> int:
    session_id = None
    process = None
    try:
        session_id = client.create_session(details, opc_retry_token=str(uuid.uuid4())).data.id
        print(f"Created session {session_id}; waiting for ACTIVE...", flush=True)
        session = wait_active(client, session_id)
        command = tunnel_command(ssh, key, port, target_ip, session)
        inner = [ssh, "-i", str(key), "-o", "IdentitiesOnly=yes",
                 "-o", f"HostKeyAlias=fluxnova-controller-{target_ip}",
                 "-p", str(port), f"{username}@127.0.0.1"]
        print(f"Starting 127.0.0.1:{port} -> {target_ip}:22 (TTL {details.session_ttl_in_seconds}s).", flush=True)
        print("Complete any SSH host-key prompt below. Waiting for controller SSH to respond; "
              "startup retries are bounded to 120 seconds.", flush=True)
        print("Ctrl+C closes the tunnel and deletes this temporary session.", flush=True)
        deadline = time.monotonic() + STARTUP_TIMEOUT
        for attempt in range(1, STARTUP_ATTEMPTS + 1):
            # Reuse the same session: ACTIVE can precede SSH authorization readiness.
            # Inherit the terminal so host-key/passphrase prompts remain interactive.
            process = subprocess.Popen(command)
            if wait_for_forward(process, port, deadline):
                break
            result = process.wait()
            if (result != 255 or attempt == STARTUP_ATTEMPTS
                    or time.monotonic() + STARTUP_RETRY_DELAY >= deadline):
                raise RuntimeError(f"SSH exited with status {result} before the tunnel was ready "
                                   f"(attempt {attempt}). Check the SSH error above, matching key pair, "
                                   "Bastion client IP allowlist and session TTL.")
            print(f"SSH is not ready (attempt {attempt}/{STARTUP_ATTEMPTS}); "
                  f"retrying this session in {STARTUP_RETRY_DELAY}s. "
                  "A new session may need time to accept its key.", flush=True)
            time.sleep(STARTUP_RETRY_DELAY)
        print(f"Ready: controller SSH responded through 127.0.0.1:{port}. Keep this terminal open.\n"
              "You can now run this in a second terminal:\n" + display_command(inner), flush=True)
        result = process.wait()
        if result:
            print("SSH exited unsuccessfully. Check the Bastion client IP allowlist, session TTL, "
                  "and controller readiness.", file=sys.stderr)
        return result
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if session_id:
            try:
                client.delete_session(session_id)
                print("Bastion session deletion requested.", flush=True)
            except Exception as exc:
                # The short server-side TTL also bounds a session after lost connectivity.
                print(f"Could not delete session {session_id}: {getattr(exc, 'message', str(exc))}. "
                      "Delete it in OCI, or let its TTL expire.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, help="Applied environment_contract JSON (UTF-8)")
    parser.add_argument("--terraform-dir", type=Path, default=ROOT / "infra" / "oci-reference")
    parser.add_argument("--profile", default="BENCHMARK")
    parser.add_argument("--config-file", type=Path, default=Path.home() / ".oci" / "config")
    parser.add_argument("--key", type=Path, default=Path.home() / ".ssh" / "fluxnova_bench")
    parser.add_argument("--local-port", type=int, default=2222)
    parser.add_argument("--ttl", type=int, default=1800, help="Session seconds (1800-3600)")
    args = parser.parse_args(argv)
    if not 1024 <= args.local_port <= 65535:
        parser.error("--local-port must be between 1024 and 65535")
    if not 1800 <= args.ttl <= 3600:
        parser.error("--ttl must be between 1800 and 3600")
    try:
        contract = load_contract(args.contract, args.terraform_dir)
        bastion_id, region, target_ip, username = connection_details(contract)
        ssh = shutil.which("ssh")
        if not ssh:
            raise ValueError("OpenSSH (ssh) is not on PATH.")
        key = args.key.expanduser().resolve()
        public_path = Path(str(key) + ".pub")
        if not key.is_file() or not public_path.is_file():
            raise ValueError(f"Both {key} and {public_path} must exist.")
        public_key = public_path.read_text(encoding="utf-8").strip()
        if not public_key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
            raise ValueError("Expected an OpenSSH public key in the .pub file.")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.local_port))
        try:
            import oci
        except ImportError:
            raise ValueError("Install the OCI SDK: python -m pip install oci==2.187.1") from None
        cfg = oci.config.from_file(str(args.config_file.expanduser()), args.profile)
        cfg["region"] = region
        token = Path(cfg["security_token_file"]).expanduser().read_text().strip()
        signer = oci.auth.signers.SecurityTokenSigner(
            token, oci.signer.load_private_key_from_file(os.path.expanduser(cfg["key_file"])),
        )
        client = oci.bastion.BastionClient(cfg, signer=signer, timeout=(10, 30))
        bastion = client.get_bastion(bastion_id).data
        if bastion.lifecycle_state != "ACTIVE":
            raise ValueError(f"Bastion is {bastion.lifecycle_state}; it must be ACTIVE.")
        if args.ttl > bastion.max_session_ttl_in_seconds:
            raise ValueError("Requested TTL exceeds the Bastion's configured maximum.")
        print("Allowed public client CIDRs: " + ", ".join(bastion.client_cidr_block_allow_list), flush=True)
        details = oci.bastion.models.CreateSessionDetails(
            bastion_id=bastion_id, display_name="fluxnova-controller-forward",
            key_type="PUB", session_ttl_in_seconds=args.ttl,
            key_details=oci.bastion.models.PublicKeyDetails(public_key_content=public_key),
            target_resource_details=oci.bastion.models.CreatePortForwardingSessionTargetResourceDetails(
                target_resource_private_ip_address=target_ip, target_resource_port=22,
            ),
        )
        return run_session(client, details, ssh, key, args.local_port, target_ip, username)
    except KeyboardInterrupt:
        print("\nTunnel stopped.")
        return 130
    except Exception as exc:
        print("Error: " + getattr(exc, "message", str(exc)), file=sys.stderr)
        print(f"If the OCI session expired, run: oci session authenticate --profile-name {args.profile}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
