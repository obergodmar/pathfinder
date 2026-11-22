"""Bounded, read-only system command adapters."""

import concurrent.futures
import ipaddress
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time


def run(argv, timeout=10, input_text=None):
  try:
    result = subprocess.run(
      argv,
      input=input_text,
      capture_output=True,
      text=True,
      timeout=timeout,
      env={**os.environ, "LC_ALL": "C"},
      check=False,
    )
    return result.returncode, result.stdout, result.stderr[:1000]
  except FileNotFoundError:
    return 127, "", f"Executable unavailable: {argv[0]}"
  except subprocess.TimeoutExpired:
    return 124, "", "Command timed out"
  except OSError as exc:
    return 126, "", str(exc)


def parse_ping(code, output, error):
  loss = re.search(r"([\d.]+)%\s+packet loss", output)
  timing = re.search(r"(?:rtt|round-trip).*?=\s*[\d.]+/([\d.]+)/", output)
  fraction = float(loss.group(1)) / 100 if loss else None
  if fraction is not None and fraction < 1 and timing:
    status = "reachable"
  elif fraction == 1:
    status = "unreachable"
  else:
    status = "unknown"
  return {
    "status": status,
    "loss": fraction,
    "rtt_ms": float(timing.group(1)) if timing else None,
    "error": error or (f"ping exit {code}" if status == "unknown" else None),
  }


def ping(address, count=3, timeout=5):
  ipaddress.ip_address(address)
  binary = os.environ.get("PATHFINDER_PING", "ping")
  args = [binary, "-n", "-c", str(count)]
  if platform.system() == "Darwin":
    args += ["-W", str(timeout * 1000)]
  else:
    args += ["-W", str(timeout)]
  return parse_ping(*run(args + [address], timeout=count * (timeout + 1) + 2))


def route(address):
  if platform.system() != "Linux":
    return {"status": "unsupported", "reason": "Runtime route adapter currently requires Linux"}
  code, output, error = run([os.environ.get("PATHFINDER_IP", "ip"), "-j", "route", "get", address])
  if code:
    return {"status": "unknown", "reason": error}
  try:
    rows = json.loads(output)
    return {"status": "observed", **rows[0]}
  except (ValueError, IndexError, TypeError):
    return {"status": "unknown", "reason": "Invalid route output"}


def runtime_peers(topology, source):
  host = topology.host(source)
  binary = os.environ.get(
    "PATHFINDER_AWG" if host.get("backend") == "amneziawg" else "PATHFINDER_WG",
    "awg" if host.get("backend") == "amneziawg" else "wg",
  )
  interface = host.get("interface", "mesh")
  if not re.fullmatch(r"[A-Za-z0-9_.-]+", interface):
    raise ValueError("Invalid interface")
  # Never request showconf or dump: these can expose private/preshared keys.
  code, output, error = run([binary, "show", interface, "allowed-ips"])
  if code:
    return {"status": "unknown", "reason": error, "peers": []}
  names = {h.get("public_key"): name for name, h in topology.hosts.items() if h.get("public_key")}
  peers = []
  try:
    for line in output.splitlines():
      fields = line.split()
      prefixes = [p.strip(",") for p in fields[1:] if p != "(none)"]
      for prefix in prefixes:
        ipaddress.ip_network(prefix, strict=False)
      peers.append({"name": names.get(fields[0], "unknown-peer"), "allowed_ips": prefixes})
  except (ValueError, IndexError):
    return {"status": "unknown", "reason": "Invalid allowed-ips output", "peers": []}
  return {"status": "observed", "peers": peers}


def observed_peer(runtime, address):
  if runtime["status"] != "observed":
    return None
  ip = ipaddress.ip_address(address)
  matches = [
    (ipaddress.ip_network(p, strict=False).prefixlen, peer["name"])
    for peer in runtime["peers"]
    for p in peer["allowed_ips"]
    if ip in ipaddress.ip_network(p, strict=False)
  ]
  if not matches:
    return "no-peer"
  size = max(n for n, _ in matches)
  names = {name for n, name in matches if n == size}
  return names.pop() if len(names) == 1 else "ambiguous"


def probe_one(topology, source, target, network, runtime, count, timeout):
  host = topology.host(target)
  address = (
    host["address"]
    if network == "mesh"
    else (
      topology.host(source).get("endpoints", {}).get(target)
      if network == "endpoint"
      else host.get("lan_address")
    )
  )
  base = {"source": source, "target": target, "network": network, "address": address}
  if not address:
    return {**base, "status": "unknown", "error": f"No declared {network} address"}
  try:
    # DNS is resolved outside ping, preserving the exact measured address.
    try:
      address = str(ipaddress.ip_address(address))
    except ValueError:
      code, resolved, _ = run(
        [
          sys.executable,
          "-c",
          "import socket,sys; print(socket.getaddrinfo(sys.argv[1], None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0])",
          address,
        ],
        timeout=5,
      )
      if code:
        raise OSError("DNS resolution failed")
      address = str(ipaddress.ip_address(resolved.strip()))
  except (OSError, IndexError, ValueError):
    return {**base, "status": "unknown", "error": "Address resolution failed"}
  routing = route(address)
  peer = observed_peer(runtime, address) if network == "mesh" else None
  expected = topology.path(source, target) if network == "mesh" else None
  interface = topology.host(source).get("interface", "mesh")
  conflict = network == "mesh" and (
    (routing.get("status") == "observed" and routing.get("dev") != interface)
    or (
      peer is not None and expected and len(expected["nodes"]) > 1 and peer != expected["nodes"][1]
    )
  )
  return {
    **base,
    "address": address,
    **ping(address, count, timeout),
    "route": routing,
    "expected_path": expected,
    "runtime_peer": peer,
    "route_conflict": conflict,
    "endpoint_uses_mesh": network == "endpoint" and routing.get("dev") == interface,
    "evidence": "icmp_only" if network == "endpoint" else "probe_with_route_context",
  }


def probe(topology, source, targets, networks=("mesh",), count=3, timeout=3, workers=4):
  topology.host(source)
  runtime = runtime_peers(topology, source)
  jobs = [(target, network) for target in targets if target != source for network in networks]
  with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
    futures = [
      pool.submit(probe_one, topology, source, target, network, runtime, count, timeout)
      for target, network in jobs
    ]
    results = [future.result() for future in futures]
  return {
    "schema_version": 1,
    "source": source,
    "timestamp": time.time(),
    "topology_hash": topology.fingerprint,
    "runtime": runtime,
    "results": results,
  }


def remote_probe(topology, source, targets, networks, count=3, timeout=3, workers=4):
  host = topology.host(source)
  alias = host.get("ssh_alias", source)
  if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", alias):
    raise ValueError(f"Invalid SSH alias: {alias!r}")
  command = [
    "pathfinder",
    "--config",
    "-",
    "--source",
    source,
    "probe",
    "--json",
    "--count",
    str(count),
    "--timeout",
    str(timeout),
    "--workers",
    str(workers),
    "--networks",
    ",".join(networks),
    *targets,
  ]
  # OpenSSH sends a shell command. Quote every word, including inventory names.
  code, output, error = run(
    [
      os.environ.get("PATHFINDER_SSH", "ssh"),
      "-o",
      "BatchMode=yes",
      "-o",
      "ConnectTimeout=5",
      "-o",
      "ServerAliveInterval=5",
      "-o",
      "ServerAliveCountMax=2",
      alias,
      shlex.join(command),
    ],
    timeout=20 + math_timeout(len(targets) * len(networks), count, timeout, workers),
    input_text=json.dumps(topology.data),
  )
  if not code:
    try:
      snapshot = json.loads(output)
      if snapshot["source"] == source and snapshot["topology_hash"] == topology.fingerprint:
        return snapshot
    except (ValueError, KeyError, TypeError):
      pass
  return {
    "schema_version": 1,
    "source": source,
    "timestamp": time.time(),
    "topology_hash": topology.fingerprint,
    "results": [],
    "error": error or "Invalid remote snapshot",
    "status": "collection_failed",
  }


def math_timeout(jobs, count, timeout, workers):
  return ((jobs + workers - 1) // workers) * (count * (timeout + 1) + 22)
