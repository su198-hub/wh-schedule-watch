"""Watch the White House press pool relay on Bluesky for the President's next-day schedule.

Every run (started every few minutes in the evening, US Eastern time):
  1. reads the newest posts of whpresspool.bsky.social (public API, no login), which relays the White House press
     pool emails, including the evening schedule for the next day;
  2. logs each new schedule post: when it was posted, when this watcher first saw it, the days it covers;
  3. for a new post that covers a later day, posted on a weekday evening (Mon-Fri, 17:00-23:59 ET), sends a
     workflow_dispatch to the workflow named by the secrets (TARGET_REPO, TARGET_WORKFLOW, TARGET_JOB, DISPATCH_TOKEN);
  4. records when Roll Call Factbase's public calendar JSON first lists each logged day (how far behind it is).

Standard library only. Reads public data; writes log.csv and seen.json in this repo.

    python watch.py           # one pass
    python watch.py --init    # mark the current posts as seen (no dispatch)
"""
import csv, datetime as dt, json, os, re, sys, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BSKY = "https://public.api.bsky.app/xrpc/"
ACTOR = "whpresspool.bsky.social"
FACTBASE = "https://media-cdn.factba.se/rss/json/trump/calendar-full.json"
HERE = os.path.dirname(os.path.abspath(__file__))
SEEN, LOG = os.path.join(HERE, "seen.json"), os.path.join(HERE, "log.csv")
FIELDS = ["uri", "posted_utc", "seen_utc", "lag_min", "days", "dispatched", "factbase_listed_utc"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


def get(url, params=None, timeout=30):
    if params: url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "wh-schedule-watch (read-only)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def days_covered(text, year):
    """dates a schedule post lists: '📅 FRIDAY, NOVEMBER 13' headers, else the title's 'Thursday, Nov. 12, 2026'"""
    out = set()
    for mon, d in re.findall(r"(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY),\s+([A-Z]+)\s+(\d{1,2})", text.upper()):
        if mon in MONTHS: out.add(dt.date(year, MONTHS.index(mon) + 1, int(d)))
    if not out:
        m = re.search(r"(?:day), (\w{3})\w*\.? (\d{1,2}), (\d{4})", text)
        if m:
            mon = next((i for i, x in enumerate(MONTHS, 1) if x.startswith(m.group(1).upper())), None)
            if mon: out.add(dt.date(int(m.group(3)), mon, int(m.group(2))))
    return sorted(out)


def schedule_posts():
    """[(uri, createdAt, text)] of the newest schedule posts; thread replies by the same account joined"""
    feed = get(BSKY + "app.bsky.feed.getAuthorFeed", {"actor": ACTOR, "limit": 30}).get("feed", [])
    out = []
    for it in feed:
        p = it["post"]; txt = p["record"].get("text", "")
        if not re.search(r"Trump.s (Daily|Weekend) Schedule", txt[:80]): continue
        th = get(BSKY + "app.bsky.feed.getPostThread", {"uri": p["uri"], "depth": 10})["thread"]
        texts, node = [], th
        while node:
            texts.append(node["post"]["record"].get("text", ""))
            node = next((c for c in node.get("replies") or [] if c.get("post", {}).get("author", {}).get("handle") == ACTOR), None)
        out.append((p["uri"], p["record"]["createdAt"], "\n".join(texts)))
    return out


def dispatch():
    tok, repo = os.environ.get("DISPATCH_TOKEN"), os.environ.get("TARGET_REPO")
    if not tok or not repo: return "no target configured"
    wf, job = os.environ.get("TARGET_WORKFLOW", "live.yml"), os.environ.get("TARGET_JOB", "sched")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches", method="POST",
                                 data=json.dumps({"ref": "main", "inputs": {"job": job}}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return f"dispatched (HTTP {r.status})"
    except Exception as ex:
        return f"dispatch failed: {str(ex)[:120]}"


def main(init=False):
    now = dt.datetime.now(dt.timezone.utc); now_et = now.astimezone(ET)
    seen = json.load(open(SEEN)) if os.path.exists(SEEN) else {"uris": []}
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    new = [x for x in schedule_posts() if x[0] not in seen["uris"]]
    for uri, posted, text in sorted(new, key=lambda x: x[1]):
        t_post = dt.datetime.fromisoformat(posted.replace("Z", "+00:00"))
        days = days_covered(text, t_post.astimezone(ET).year)
        evening = now_et.weekday() <= 4 and 17 <= now_et.hour <= 23
        later = any(d > now_et.date() for d in days)
        res = "init" if init else (dispatch() if (evening and later) else "not a weekday evening or no later day")
        rows.append(dict(uri=uri, posted_utc=t_post.isoformat(timespec="seconds"), seen_utc=now.isoformat(timespec="seconds"),
                         lag_min=round((now - t_post).total_seconds() / 60, 1), days=" ".join(map(str, days)), dispatched=res,
                         factbase_listed_utc=""))
        seen["uris"].append(uri)
        print(f"new schedule post {posted} covering {days}: {res}")
    pending = [r for r in rows if not r["factbase_listed_utc"] and r["days"]]
    if pending:
        try:
            listed = {x.get("date") for x in get(FACTBASE, timeout=60) if x.get("type") == "President Schedule"}
            for r in pending:
                if any(d in listed for d in r["days"].split()):
                    r["factbase_listed_utc"] = now.isoformat(timespec="seconds")
        except Exception as ex:
            print(f"factbase not read ({str(ex)[:80]})")
    seen["uris"] = seen["uris"][-200:]
    json.dump(seen, open(SEEN, "w"), indent=0)
    with open(LOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    if not new: print("no new schedule post")


if __name__ == "__main__":
    main(init="--init" in sys.argv)
