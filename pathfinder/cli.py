"""Agent-friendly commands and optional curses view."""

import argparse
import concurrent.futures
import json
import os
import socket
import sys
from pathlib import Path

from .model import Topology, candidates
from .probes import probe, remote_probe
from .ui import lines, print_result, tui


def bounded(low, high):
  def parse(value):
    number = int(value)
    if not low <= number <= high:
      raise argparse.ArgumentTypeError(f"Expected {low}..{high}")
    return number

  return parse


def parser():
  root = argparse.ArgumentParser(
    description="Read-only mesh diagnostics and relay candidate ranking"
  )
  root.add_argument(
    "--config", default=os.environ.get("PATHFINDER_CONFIG", "/etc/pathfinder/topology.json")
  )
  root.add_argument(
    "--source", default=os.environ.get("PATHFINDER_HOST", socket.gethostname().split(".")[0])
  )
  commands = root.add_subparsers(dest="command", required=True)
  for name in ("topology", "explain", "probe", "scan", "recommend", "tui", "status"):
    sub = commands.add_parser(name)
    sub.add_argument("--json", action="store_true")
    if name == "explain":
      sub.add_argument("source")
      sub.add_argument("target")
    if name in ("probe", "scan"):
      sub.add_argument("targets", nargs="*", help="Inventory names; defaults to every other host")
      sub.add_argument(
        "--networks", default="mesh,endpoint", help="Comma-separated mesh,endpoint,lan"
      )
      sub.add_argument("--count", type=bounded(1, 20), default=3)
      sub.add_argument("--timeout", type=bounded(1, 10), default=3)
      sub.add_argument("--workers", type=bounded(1, 16), default=4)
      sub.add_argument("--output", help="Save JSON snapshot/report")
    if name == "scan":
      sub.add_argument(
        "--sources", nargs="+", help="Hosts to query over SSH; defaults to all managed hosts"
      )
      sub.add_argument("--ssh-workers", type=bounded(1, 8), default=2)
    if name in ("recommend", "tui", "status"):
      sub.add_argument("--measurements", help="JSON output from probe or scan")
    if name == "recommend":
      sub.add_argument("source")
      sub.add_argument("target")
      sub.add_argument("--max-hops", type=bounded(1, 4), default=3)
      sub.add_argument("--limit", type=bounded(1, 50), default=10)
      sub.add_argument("--max-age", type=bounded(1, 86400), default=300)
  return root


def read_json(path):
  return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text())


def main():
  args = parser().parse_args()
  try:
    topology = Topology(read_json(args.config))
    snapshots = []
    if getattr(args, "measurements", None):
      data = read_json(args.measurements)
      snapshots = data.get("snapshots", [data])
    if args.command == "topology":
      result = {
        "schema_version": 1,
        "topology_hash": topology.fingerprint,
        "topology": topology.data,
      }
    elif args.command == "explain":
      result = topology.explain(args.source, args.target)
    elif args.command in ("probe", "scan"):
      targets = args.targets or sorted(topology.hosts)
      for name in targets:
        topology.host(name)
      networks = args.networks.split(",")
      if not networks or not set(networks) <= {"mesh", "endpoint", "lan"}:
        raise ValueError("Networks must be mesh,endpoint,lan")
      if args.command == "probe":
        result = probe(
          topology, args.source, targets, networks, args.count, args.timeout, args.workers
        )
      else:
        sources = args.sources or [n for n, h in topology.hosts.items() if h.get("managed", True)]
        for name in sources:
          topology.host(name)

        def collect(source):
          operation = probe if source == args.source else remote_probe
          return operation(
            topology, source, targets, networks, args.count, args.timeout, args.workers
          )

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.ssh_workers) as pool:
          snapshots = list(pool.map(collect, sources))
        result = {
          "schema_version": 1,
          "topology_hash": topology.fingerprint,
          "snapshots": snapshots,
        }
      if args.output:
        # Saved reports contain network inventory. Do not make them world-readable.
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
          json.dump(result, stream, indent=2, allow_nan=False)
    elif args.command == "recommend":
      result = candidates(
        topology, snapshots, args.source, args.target, args.max_hops, args.limit, args.max_age
      )
    elif args.command == "tui" and not args.json:
      tui(topology, snapshots)
      return 0
    else:
      if not args.json:
        print("\n".join(lines(topology, snapshots)))
        return 0
      result = {
        "topology_hash": topology.fingerprint,
        "snapshots": snapshots,
        "paths": [topology.explain(a, b) for a in topology.hosts for b in topology.hosts if a != b],
      }
    print_result(result, args.json)
    return 0
  except (OSError, ValueError, KeyError, TypeError) as exc:
    print(json.dumps({"error": str(exc)}), file=sys.stderr)
    return 2
  except KeyboardInterrupt:
    return 130


if __name__ == "__main__":
  sys.exit(main())
