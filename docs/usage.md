# Usage and evidence

## Inventory

Schema version 1 contains `hosts`, indexed by stable host names. Each host has:

- `address`: mesh IP; `public_key`: public peer identity, never a private key.
- `interface`, `backend`: local mesh interface and `amneziawg` or `wireguard`.
- `peers`: effective entries with `name`, `allowed_ips`, and optional `endpoint`.
- `endpoints`: destination host to endpoint hostname/IP, including disabled direct peers.
- `relay_candidate`: whether to consider this host as an intermediate hop.
- `managed`, `ssh_alias`, optional `lan_address`, and informational `overrides`.

Generate this data from the same configuration functions that generate tunnels.
Additional networks and public relay metadata are preserved by `topology`; production
route calculation currently uses the primary mesh's effective AllowedIPs only.

## Measurements

`probe` runs locally against any inventory host; `scan` collects from multiple sources
using their existing SSH aliases. It requires installed Pathfinder and working SSH
credentials on the controlling machine, not SSH credentials on every remote host.
SSH agent forwarding and host-key-check bypasses are not enabled.
Use explicit `--sources` for external/mobile devices excluded by the default managed set.
`--source` identifies the current machine; it does not move execution to that host.

Default networks are `mesh,endpoint`. Defaults are three packets, a one-second
reply timeout, eight concurrent probes per source and four SSH sources at once.
Use `--timeout`, `--workers`, `--ssh-workers`, and `--sources` to control scan cost. Use `--networks lan` for declared LAN addresses.
Mesh probes follow current routing, including configured relays. Endpoint probes test
ICMP to the resolved endpoint, not its AWG UDP port. An endpoint routed through mesh is
flagged and excluded from exploratory direct-edge recommendations.

Runtime peer inspection can require elevated privileges. Without access, diagnostics
remain useful but evidence says configured rather than observed direct legs. The
program only requests `allowed-ips`, never secret-bearing dump/showconf output.
Linux route observation uses iproute2. Other platforms can ping and view the topology;
runtime route observation is explicitly unsupported until a platform adapter exists.
On Android, OS/VPN policy can prevent ping or tunnel inspection; this is unknown, not down.

## Recommendations

`recommend` evaluates simple paths up to three hops by default (maximum four), using
fresh observations from the same topology hash. Default maximum age is fifteen minutes.
Known direct mesh legs require successful probes initiated in both directions and
matching configured direct paths; observed route conflicts exclude the leg.
Sort order is estimated loss, summed conservative leg RTT, then hop count.
These are snapshot rankings, not a guarantee of globally optimal routing.

Two separate result lists:

- `candidates`: paths assembled from measured configured/observed direct mesh legs.
- `exploratory_candidates`: paths that additionally use successful endpoint ICMP probes
  with an observed route outside mesh. These may reveal alternatives hidden by overrides,
  but require actual AWG validation. Endpoint RTT is only a preliminary estimate.

Summed leg RTT and compounded loss are estimates, not measured end-to-end performance.
ICMP can be blocked while AWG works. A successful ping to a server's public IP does not
prove that a new AWG handshake will succeed. Disabled direct peers cannot be tested as
AWG peers without configuring an isolated test tunnel or changing live configuration;
Pathfinder does neither. Missing observations are never converted into failed edges.

Before selecting a relay, change outbound and return AllowedIPs, receiving peers'
source-address permissions, and forwarding rules declaratively. Retain unrelated
prefixes. After deployment, measure the complete path in both directions.

## Interface

All commands support `--json`; errors are JSON on stderr and exit status 2.
Probe failures are data, not command failures. Scan collection failures produce empty
source observations with an error, not a row of unreachable destinations.
TUI arrows select a source; r toggles relay recommendations; Tab changes the
recommendation target; j/k scroll; q exits. Saved observations are
static: rerun scan to refresh them. `status` is the noninteractive equivalent.
Use `recommend --json` for machine-readable candidates and their required changes.
