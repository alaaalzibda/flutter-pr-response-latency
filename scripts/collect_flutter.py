"""Collect Flutter PR data from the GitHub GraphQL API.

Stdlib only, so it runs anywhere. Writes newline-delimited JSON, one PR per line,
and checkpoints the pagination cursor so it can be resumed after interruption.
"""
import json, os, pathlib, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = (ROOT / "github_token.txt").read_text().strip()
OUT = ROOT / "data" / "raw" / "flutter_prs.jsonl"
STATE = ROOT / "data" / "raw" / "collect_state.json"
TARGET = int(os.environ.get("TARGET_PRS", "8000"))
PAGE = 25

QUERY = """
query($cursor: String) {
  rateLimit { remaining cost resetAt }
  repository(owner: "flutter", name: "flutter") {
    pullRequests(first: %d, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number createdAt closedAt mergedAt state isDraft
        title bodyText
        additions deletions changedFiles
        author { login __typename }
        authorAssociation
        commits { totalCount }
        comments(first: 25) {
          nodes { createdAt authorAssociation author { login __typename } }
        }
        reviews(first: 15) {
          nodes { createdAt authorAssociation author { login __typename } }
        }
      }
    }
  }
}
""" % PAGE


def post(cursor):
    body = json.dumps({"query": QUERY, "variables": {"cursor": cursor}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
            "User-Agent": "pr-latency-experiment",
        },
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.load(r)
            if "errors" in payload:
                msg = json.dumps(payload["errors"])[:300]
                # RATE_LIMITED and transient server errors are worth waiting out
                print("api errors:", msg, flush=True)
                time.sleep(20 * (attempt + 1))
                continue
            return payload["data"]
        except urllib.error.HTTPError as e:
            print("http", e.code, flush=True)
            time.sleep(20 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            print("err", type(e).__name__, e, flush=True)
            time.sleep(10 * (attempt + 1))
    raise SystemExit("giving up after repeated failures")


def main():
    cursor, collected = None, 0
    if STATE.exists():
        s = json.loads(STATE.read_text())
        cursor, collected = s.get("cursor"), s.get("collected", 0)
        print(f"resuming at {collected} PRs", flush=True)
    mode = "a" if collected else "w"
    with OUT.open(mode) as fh:
        while collected < TARGET:
            data = post(cursor)
            rl = data["rateLimit"]
            prs = data["repository"]["pullRequests"]
            for node in prs["nodes"]:
                fh.write(json.dumps(node) + "\n")
            collected += len(prs["nodes"])
            fh.flush()
            cursor = prs["pageInfo"]["endCursor"]
            STATE.write_text(json.dumps({"cursor": cursor, "collected": collected}))
            if collected % 500 < PAGE:
                print(f"{collected} PRs | graphql budget left {rl['remaining']}", flush=True)
            if not prs["pageInfo"]["hasNextPage"]:
                print("reached end of repository", flush=True)
                break
            if rl["remaining"] < 50:
                print("budget nearly exhausted, pausing 60s", flush=True)
                time.sleep(60)
    print(f"DONE: {collected} PRs -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
