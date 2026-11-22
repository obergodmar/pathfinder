"""Declared routing and evidence-based candidate ranking; no network mutations."""

import hashlib
import ipaddress
import json
import math
import re
import time


class Topology:
  def __init__(self, data):
    if data.get("schema_version") != 1 or not isinstance(data.get("hosts"), dict):
      raise ValueError("Expected topology schema_version 1 and hosts object")
    self.data = data
    self.hosts = data["hosts"]
    for name, host in self.hosts.items():
      if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(f"Invalid host name: {name!r}")
      ipaddress.ip_address(host["address"])
      for peer in host.get("peers", []):
        if peer["name"] not in self.hosts:
          raise ValueError(f"Unknown peer {peer['name']} on {name}")
        for prefix in peer["allowed_ips"]:
          ipaddress.ip_network(prefix, strict=False)
    self.fingerprint = hashlib.sha256(
      json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

  def host(self, name):
    if name not in self.hosts:
      raise ValueError(f"Unknown host: {name}")
    return self.hosts[name]

  def next_hop(self, source, address):
    destination = ipaddress.ip_address(address)
    matches = []
    for peer in self.host(source).get("peers", []):
      for prefix in peer["allowed_ips"]:
        network = ipaddress.ip_network(prefix, strict=False)
        if destination in network:
          matches.append((network.prefixlen, peer["name"]))
    if not matches:
      return None
    length = max(length for length, _ in matches)
    winners = {name for size, name in matches if size == length}
    if len(winners) != 1:
      raise ValueError(f"Ambiguous AllowedIPs on {source} for {address}")
    return winners.pop()

  def path(self, source, target):
    address = self.host(target)["address"]
    self.host(source)
    nodes = [source]
    while nodes[-1] != target:
      try:
        hop = self.next_hop(nodes[-1], address)
      except ValueError as exc:
        return {"nodes": nodes, "status": "ambiguous", "reason": str(exc)}
      if hop is None:
        return {"nodes": nodes, "status": "no_route"}
      if hop in nodes:
        return {"nodes": nodes + [hop], "status": "loop"}
      nodes.append(hop)
    return {"nodes": nodes, "status": "configured"}

  def explain(self, source, target):
    forward = self.path(source, target)
    reverse = self.path(target, source)
    return {
      "source": source,
      "target": target,
      "forward": forward,
      "reverse": reverse,
      "direct_configured": forward["nodes"] == [source, target]
      and forward["status"] == "configured",
      "overrides": self.host(source).get("overrides", []),
      "evidence": "declared_only",
      "warning": "Routing intent does not prove deployed routes, forwarding or reachability.",
    }


def valid_measurements(topology, snapshots, max_age=900, now=None):
  now = time.time() if now is None else now
  records = {}
  rejected = []
  for snapshot in snapshots:
    source = snapshot.get("source")
    age = now - snapshot.get("timestamp", 0)
    if (
      snapshot.get("topology_hash") != topology.fingerprint
      or source not in topology.hosts
      or age < -30
      or age > max_age
    ):
      rejected.append({"source": source, "reason": "stale, future, unknown or mismatched topology"})
      continue
    for result in snapshot.get("results", []):
      target = result.get("target")
      network = result.get("network")
      if target not in topology.hosts:
        continue
      key = (source, target, network)
      if key not in records or snapshot["timestamp"] > records[key][0]:
        records[key] = (snapshot["timestamp"], result)
  return {key: result for key, (_, result) in records.items()}, rejected


def candidates(topology, snapshots, source, target, max_hops=3, limit=10, max_age=900):
  """Rank simple paths; require independently measured directed legs both ways.

  RTT sums are estimates, not measured end-to-end latency. Transit ACLs and
  cryptokey source validation still require changes and an end-to-end test.
  """
  topology.host(source)
  topology.host(target)
  if source == target:
    raise ValueError("Source and target must differ")
  records, rejected = valid_measurements(topology, snapshots, max_age)
  edges = {}
  exclusions = []
  for (left, right, network), result in records.items():
    if network != "mesh" or left == right:
      continue
    back = records.get((right, left, "mesh"))
    reasons = []
    for a, b, probe in [(left, right, result), (right, left, back)]:
      if not probe or probe.get("status") != "reachable":
        reasons.append(f"{a}->{b}: no successful fresh probe")
        continue
      route = topology.path(a, b)
      if route["status"] != "configured" or route["nodes"] != [a, b]:
        reasons.append(f"{a}->{b}: configured path is not a direct leg")
      if probe.get("route_conflict"):
        reasons.append(f"{a}->{b}: observed route conflicts with configuration")
      if probe.get("runtime_peer") not in (None, b):
        reasons.append(f"{a}->{b}: observed peer differs")
      rtt = probe.get("rtt_ms")
      loss = probe.get("loss")
      if not isinstance(rtt, (int, float)) or not math.isfinite(rtt) or rtt < 0:
        reasons.append(f"{a}->{b}: missing or invalid RTT")
      if not isinstance(loss, (int, float)) or not math.isfinite(loss) or not 0 <= loss < 1:
        reasons.append(f"{a}->{b}: missing or invalid loss")
    if reasons:
      exclusions.append({"source": left, "target": right, "reasons": reasons})
      continue
    edges[left, right] = {
      "rtt_ms": max(result["rtt_ms"], back["rtt_ms"]),
      "loss": max(result["loss"], back["loss"]),
      "runtime_verified": result.get("runtime_peer") == right
      and back.get("runtime_peer") == left
      and result.get("route", {}).get("status") == "observed"
      and back.get("route", {}).get("status") == "observed",
    }

  def enumerate_paths(edge_map, evidence_kind):
    paths = []

    def visit(nodes, latency, survival, verified):
      if nodes[-1] == target:
        paths.append(
          {
            "path": nodes,
            "estimated_rtt_ms": round(latency, 3),
            "estimated_loss": round(1 - survival, 6),
            "evidence": evidence_kind
            if evidence_kind
            else ("observed_direct_legs" if verified else "configured_direct_legs"),
            "end_to_end_verified": False,
            "required_changes": [
              "Update forward and return AllowedIPs without removing unrelated prefixes.",
              "Allow original source addresses on receiving peers at every transit hop.",
              "Enable forwarding and permit this source/destination pair on relay firewalls.",
              "Deploy declaratively and verify end-to-end reachability in both directions.",
            ]
            if len(nodes) > 2
            else [],
          }
        )
        return
      if len(nodes) - 1 >= max_hops:
        return
      for name in sorted(topology.hosts):
        edge = edge_map.get((nodes[-1], name))
        if not edge or name in nodes:
          continue
        if name != target and not topology.hosts[name].get("relay_candidate", False):
          continue
        visit(
          nodes + [name],
          latency + edge["rtt_ms"],
          survival * (1 - edge["loss"]),
          verified and edge["runtime_verified"],
        )

    visit([source], 0, 1, True)
    paths.sort(
      key=lambda p: (p["estimated_loss"], p["estimated_rtt_ms"], len(p["path"]), p["path"])
    )
    return paths[:limit]

  paths = enumerate_paths(edges, None)
  exploratory = dict(edges)
  for (left, right, network), result in records.items():
    if (
      network != "endpoint"
      or result.get("status") != "reachable"
      or result.get("endpoint_uses_mesh")
    ):
      continue
    if result.get("route", {}).get("status") != "observed":
      continue
    rtt, loss = result.get("rtt_ms"), result.get("loss")
    if not isinstance(rtt, (float, int)) or not math.isfinite(rtt) or rtt < 0:
      continue
    if not isinstance(loss, (float, int)) or not math.isfinite(loss) or not 0 <= loss < 1:
      continue
    if (left, right) not in exploratory:
      exploratory[left, right] = {"rtt_ms": rtt, "loss": loss, "runtime_verified": False}
  known_paths = {tuple(p["path"]) for p in paths}
  exploratory_paths = [
    p
    for p in enumerate_paths(exploratory, "endpoint_icmp_candidate")
    if tuple(p["path"]) not in known_paths
  ]

  return {
    "schema_version": 1,
    "source": source,
    "target": target,
    "current": topology.explain(source, target),
    "ranking": "estimated loss, summed conservative leg RTT, hop count",
    "candidates": paths,
    "exploratory_candidates": exploratory_paths,
    "excluded_legs": exclusions,
    "rejected_snapshots": rejected,
    "warning": "Candidates are measured legs, not tested alternative forwarding paths. "
    "Disabled direct peers and missing observations may hide better candidates. "
    "Endpoint ICMP success does not prove AWG connectivity.",
  }
