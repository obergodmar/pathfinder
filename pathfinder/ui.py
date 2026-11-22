"""Terminal views share the same data as the JSON interface."""

import curses
import json

from .model import valid_measurements


def lines(topology, snapshots, selected=0):
  names = sorted(topology.hosts)
  records, rejected = valid_measurements(topology, snapshots)
  output = [
    "PATHFINDER | arrows: select source | j/k: scroll | q: quit",
    "Configured paths; measurements never imply an untested direct link.",
  ]
  if not names:
    return output + ["No hosts"]
  source = names[selected % len(names)]
  output += [
    f"Source: {source} | fresh observations: {len(records)} | rejected: {len(rejected)}",
    "TARGET                  MESH          RTT ms   DIRECT / ENDPOINT     CONFIGURED PATH",
  ]
  for target in names:
    if target == source:
      continue
    path = topology.path(source, target)
    probe = records.get((source, target, "mesh"), {})
    status = probe.get("status", "unmeasured")
    if probe.get("route_conflict"):
      status = "route-drift"
    endpoint = records.get((source, target, "endpoint"), {})
    direct = "configured" if path["nodes"] == [source, target] else "disabled"
    if endpoint:
      direct += "/" + endpoint.get("status", "unknown")
    output.append(
      f"{target:23} {status:13} {probe.get('rtt_ms', '-')!s:8} "
      + f"{direct:21} "
      + " -> ".join(path["nodes"])
      + f" [{path['status']}]"
    )
  errors = [f"{s.get('source')}: {s['error']}" for s in snapshots if s.get("error")]
  return output + ["", "Collection errors:"] + errors if errors else output


def tui(topology, snapshots):
  def screen(window):
    try:
      curses.curs_set(0)
    except curses.error:
      pass
    if curses.has_colors():
      curses.start_color()
      curses.use_default_colors()
      curses.init_pair(1, curses.COLOR_GREEN, -1)
      curses.init_pair(2, curses.COLOR_RED, -1)
      curses.init_pair(3, curses.COLOR_YELLOW, -1)
    selected = 0
    offset = 0
    while True:
      window.erase()
      height, width = window.getmaxyx()
      content = lines(topology, snapshots, selected)
      visible = content[:4] + content[4 + offset :]
      for row, text in enumerate(visible[: max(0, height - 1)]):
        try:
          style = curses.A_BOLD if row < 4 else 0
          if row >= 4 and curses.has_colors():
            color = (
              2
              if "unreachable" in text or "route-drift" in text
              else (1 if "reachable" in text else 3)
            )
            style |= curses.color_pair(color)
          window.addnstr(row, 0, text, max(0, width - 1), style)
        except curses.error:
          pass
      window.refresh()
      key = window.getch()
      if key in (ord("q"), 27):
        break
      if key in (curses.KEY_RIGHT, curses.KEY_DOWN):
        selected += 1
        offset = 0
      elif key in (curses.KEY_LEFT, curses.KEY_UP):
        selected -= 1
        offset = 0
      elif key == ord("j"):
        offset = min(offset + 1, max(0, len(content) - 5))
      elif key == ord("k"):
        offset = max(0, offset - 1)

  curses.wrapper(screen)


def print_result(data, as_json=False):
  if as_json:
    print(json.dumps(data, indent=2, sort_keys=True, allow_nan=False))
  elif "candidates" in data:
    print(f"Relay candidates: {data['source']} -> {data['target']}")
    print(data["warning"])
    for item in data["candidates"]:
      print(
        f"  {' -> '.join(item['path'])} | loss~{item['estimated_loss']:.1%} "
        f"| RTT~{item['estimated_rtt_ms']}ms | {item['evidence']}"
      )
    for item in data.get("exploratory_candidates", []):
      print(
        f"  EXPERIMENTAL {' -> '.join(item['path'])} | RTT~{item['estimated_rtt_ms']}ms "
        "| endpoint ICMP only; AWG and forwarding unverified"
      )
    if not data["candidates"]:
      print("  No sufficiently measured candidates. Collect both directions first.")
  else:
    print(json.dumps(data, indent=2, sort_keys=True))
