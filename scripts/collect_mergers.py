"""Fetch who merged / closed each PR, to identify accounts with write access.

Only users with write access can merge a pull request, so the set of mergers is a
reliable proxy for the project's maintainers -- the same idea the authors used
when they identified maintainers from privileged events.
"""
import json, pathlib, time, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = (ROOT / "github_token.txt").read_text().strip()
OUT = ROOT / "data" / "raw" / "flutter_actors.jsonl"
STATE = ROOT / "data" / "raw" / "actors_state.json"
TARGET = 6000
PAGE = 100

QUERY = """
query($cursor: String) {
  rateLimit { remaining }
  repository(owner: "flutter", name: "flutter") {
    pullRequests(first: %d, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        mergedBy { login __typename }
        timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
          nodes { ... on ClosedEvent { actor { login __typename } } }
        }
      }
    }
  }
}
""" % PAGE


def post(cursor):
    body = json.dumps({"query": QUERY, "variables": {"cursor": cursor}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json",
                 "User-Agent": "pr-latency-experiment"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.load(r)
            if "errors" in payload:
                print("errors:", json.dumps(payload["errors"])[:200], flush=True)
                time.sleep(15 * (attempt + 1)); continue
            return payload["data"]
        except Exception as e:
            print("err", type(e).__name__, flush=True)
            time.sleep(10 * (attempt + 1))
    raise SystemExit("failed")


cursor, done = None, 0
if STATE.exists():
    s = json.loads(STATE.read_text()); cursor, done = s["cursor"], s["done"]
with OUT.open("a" if done else "w") as fh:
    while done < TARGET:
        data = post(cursor)
        prs = data["repository"]["pullRequests"]
        for n in prs["nodes"]:
            closed = (n.get("timelineItems") or {}).get("nodes") or []
            fh.write(json.dumps({
                "number": n["number"],
                "merged_by": (n.get("mergedBy") or {}).get("login"),
                "merged_by_type": (n.get("mergedBy") or {}).get("__typename"),
                "closed_by": ((closed[0].get("actor") or {}) if closed else {}).get("login"),
            }) + "\n")
        done += len(prs["nodes"]); fh.flush()
        cursor = prs["pageInfo"]["endCursor"]
        STATE.write_text(json.dumps({"cursor": cursor, "done": done}))
        print(f"{done} | budget {data['rateLimit']['remaining']}", flush=True)
        if not prs["pageInfo"]["hasNextPage"]:
            break
print("DONE", done, flush=True)
