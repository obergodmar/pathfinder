import copy
import json
import time
import unittest
from unittest.mock import patch

from pathfinder.model import Topology, candidates, valid_measurements
from pathfinder.probes import observed_peer, parse_ping, probe_one, remote_probe
from pathfinder.ui import lines


def fixture():
  hosts = {
    name: {"address": f"10.0.0.{i}", "relay_candidate": True, "peers": []}
    for i, name in enumerate(["alpha", "beta", "gamma", "delta"], 1)
  }
  for source, host in hosts.items():
    host["peers"] = [
      {"name": name, "allowed_ips": [target["address"] + "/32"]}
      for name, target in hosts.items()
      if source != name
    ]
  return Topology({"schema_version": 1, "hosts": hosts})


def snapshots(topology):
  return [
    {
      "source": a,
      "timestamp": time.time(),
      "topology_hash": topology.fingerprint,
      "results": [
        {
          "target": b,
          "network": "mesh",
          "status": "reachable",
          "loss": 0,
          "rtt_ms": 10,
          "runtime_peer": b,
        }
        for b in topology.hosts
        if b != a
      ],
    }
    for a in topology.hosts
  ]


class Routes(unittest.TestCase):
  def test_multihop_and_reverse_are_independent(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"] = [{"name": "beta", "allowed_ips": ["10.0.0.0/24"]}]
    topology.hosts["beta"]["peers"] = [{"name": "gamma", "allowed_ips": ["10.0.0.0/24"]}]
    self.assertEqual(topology.path("alpha", "delta")["nodes"], ["alpha", "beta", "gamma", "delta"])
    self.assertEqual(topology.path("delta", "alpha")["nodes"], ["delta", "alpha"])

  def test_loop(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"] = [{"name": "beta", "allowed_ips": ["10.0.0.0/24"]}]
    topology.hosts["beta"]["peers"] = [{"name": "alpha", "allowed_ips": ["10.0.0.0/24"]}]
    self.assertEqual(topology.path("alpha", "delta")["status"], "loop")

  def test_longest_prefix_and_ambiguity(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"].append({"name": "beta", "allowed_ips": ["10.0.0.0/24"]})
    self.assertEqual(topology.next_hop("alpha", "10.0.0.4"), "delta")
    topology.hosts["alpha"]["peers"].append({"name": "gamma", "allowed_ips": ["10.0.0.4/32"]})
    self.assertEqual(topology.path("alpha", "delta")["status"], "ambiguous")

  def test_no_route(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"] = []
    self.assertEqual(topology.path("alpha", "beta")["status"], "no_route")

  def test_host_name_injection_rejected(self):
    data = copy.deepcopy(fixture().data)
    data["hosts"]["$(touch bad)"] = {"address": "10.0.0.5"}
    with self.assertRaises(ValueError):
      Topology(data)


class Recommendations(unittest.TestCase):
  def test_low_loss_before_latency(self):
    topology = fixture()
    data = snapshots(topology)
    for snapshot in data:
      for result in snapshot["results"]:
        if {snapshot["source"], result["target"]} == {"alpha", "delta"}:
          result.update(loss=0.1, rtt_ms=1)
    result = candidates(topology, data, "alpha", "delta")
    self.assertEqual(result["candidates"][0]["path"], ["alpha", "beta", "delta"])
    self.assertFalse(result["candidates"][0]["end_to_end_verified"])

  def test_missing_reverse_is_not_success(self):
    topology = fixture()
    result = candidates(topology, snapshots(topology)[:1], "alpha", "delta")
    self.assertEqual(result["candidates"], [])

  def test_routed_probe_never_becomes_direct_edge(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"] = [{"name": "beta", "allowed_ips": ["10.0.0.0/24"]}]
    result = candidates(topology, snapshots(topology), "alpha", "delta")
    self.assertTrue(result["candidates"])
    self.assertTrue(all(p["path"][1] == "beta" for p in result["candidates"]))

  def test_runtime_mismatch_excluded(self):
    topology = fixture()
    data = snapshots(topology)
    for result in data[0]["results"]:
      result["route_conflict"] = True
    self.assertFalse(candidates(topology, data, "alpha", "delta")["candidates"])

  def test_stale_and_wrong_topology_excluded(self):
    topology = fixture()
    data = snapshots(topology)
    data[0]["timestamp"] = 1
    data[1]["topology_hash"] = "wrong"
    _, rejected = valid_measurements(topology, data)
    self.assertEqual(len(rejected), 2)

  def test_non_server_never_transits(self):
    topology = fixture()
    topology.hosts["beta"]["relay_candidate"] = False
    result = candidates(topology, snapshots(topology), "alpha", "delta")
    self.assertTrue(all("beta" not in p["path"][1:-1] for p in result["candidates"]))

  def test_nan_rtt_excluded(self):
    topology = fixture()
    data = snapshots(topology)
    for result in data[0]["results"]:
      result["rtt_ms"] = float("nan")
    self.assertFalse(candidates(topology, data, "alpha", "delta")["candidates"])

  def test_endpoint_alternatives_are_separate_and_unverified(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"] = []
    data = snapshots(topology)
    data[0]["results"].append(
      {
        "target": "beta",
        "network": "endpoint",
        "status": "reachable",
        "rtt_ms": 5,
        "loss": 0,
        "route": {"status": "observed", "dev": "eth0"},
      }
    )
    result = candidates(topology, data, "alpha", "delta")
    self.assertFalse(result["candidates"])
    self.assertEqual(result["exploratory_candidates"][0]["path"], ["alpha", "beta", "delta"])
    self.assertEqual(result["exploratory_candidates"][0]["evidence"], "endpoint_icmp_candidate")
    data[0]["results"][-1]["endpoint_uses_mesh"] = True
    self.assertFalse(candidates(topology, data, "alpha", "delta")["exploratory_candidates"])


class Adapters(unittest.TestCase):
  def test_linux_and_darwin_ping(self):
    for prefix in ["rtt min/avg/max/mdev", "round-trip min/avg/max/stddev"]:
      data = parse_ping(
        0, f"3 packets transmitted, 3 received, 0% packet loss\n{prefix} = 1/2.5/4/1 ms", ""
      )
      self.assertEqual(data["rtt_ms"], 2.5)
      self.assertEqual(data["status"], "reachable")

  def test_permissions_and_timeouts_are_unknown(self):
    self.assertEqual(parse_ping(2, "", "Operation not permitted")["status"], "unknown")
    self.assertEqual(parse_ping(124, "", "Command timed out")["status"], "unknown")
    self.assertEqual(parse_ping(1, "100% packet loss", "")["status"], "unreachable")

  def test_runtime_longest_prefix(self):
    runtime = {
      "status": "observed",
      "peers": [
        {"name": "beta", "allowed_ips": ["10.0.0.0/24"]},
        {"name": "delta", "allowed_ips": ["10.0.0.4/32"]},
      ],
    }
    self.assertEqual(observed_peer(runtime, "10.0.0.4"), "delta")

  def test_endpoint_through_mesh_is_flagged(self):
    topology = fixture()
    topology.hosts["alpha"]["endpoints"] = {"delta": "192.0.2.4"}
    with (
      patch("pathfinder.probes.route", return_value={"status": "observed", "dev": "mesh"}),
      patch("pathfinder.probes.ping", return_value={"status": "reachable"}),
    ):
      data = probe_one(topology, "alpha", "delta", "endpoint", {"status": "unknown"}, 1, 1)
    self.assertTrue(data["endpoint_uses_mesh"])
    self.assertIsNone(data["runtime_peer"])

  def test_ssh_failure_does_not_mark_hosts_down(self):
    topology = fixture()
    with patch("pathfinder.probes.run", return_value=(255, "", "SSH unavailable")):
      data = remote_probe(topology, "alpha", ["beta"], ["mesh"])
    self.assertEqual(data["results"], [])
    self.assertEqual(data["status"], "collection_failed")

  def test_remote_requires_matching_identity(self):
    topology = fixture()
    with patch("pathfinder.probes.run", return_value=(0, json.dumps({"source": "wrong"}), "")):
      self.assertEqual(remote_probe(topology, "alpha", [], ["mesh"])["status"], "collection_failed")

  def test_narrow_terminal_preserves_route(self):
    topology = fixture()
    topology.hosts["alpha"]["peers"] = [{"name": "beta", "allowed_ips": ["10.0.0.0/24"]}]
    output = lines(topology, [], width=80)
    self.assertIn("  alpha -> beta -> delta [configured]", output)

  def test_empty_tui_and_small_data(self):
    self.assertIn("No hosts", lines(Topology({"schema_version": 1, "hosts": {}}), []))
    self.assertTrue(any("unmeasured" in line for line in lines(fixture(), [])))


if __name__ == "__main__":
  unittest.main()
