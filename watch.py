"""Watch the White House press pool relay on Bluesky for the President's public schedule.

Every run (started every few minutes, US Eastern time):
  1. reads the newest posts of whpresspool.bsky.social (public API, no login), which relays the White House press
     pool emails: the schedule for the next day (or the weekend), and same-day updates;
  2. logs each new schedule or update post: when it was posted, when this watcher first saw it, the days it covers;
  3. for a new post seen on a weekday evening (Mon-Fri 17:00-23:59 ET) that covers a later day, sends a workflow_dispatch
     to the workflow named by the secrets (TARGET_REPO, TARGET_WORKFLOW, TARGET_JOB, DISPATCH_TOKEN). Every other post is
     only LOGGED (morning and daytime same-day posts, "added ... to the daily guidance" updates, Sunday evening): their time
     is recorded in log.csv, nothing is dispatched. A failed dispatch is retried on the next pass (the post is not marked
     seen until it has been dispatched or five attempts have failed);
  4. records when Roll Call Factbase's public calendar JSON first lists each logged day (how far behind it is).

Standard library only. Reads public data; writes log.csv and seen.json in this repo.

    python watch.py           # one pass
    python watch.py --init    # mark the current posts as seen (no dispatch)
    python watch.py --test-dispatch   # send one dispatch with job "noop" (tests the token and target), nothing else
"""
import csv, datetime as dt, json, os, re, sys, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BSKY = "https://public.api.bsky.app/xrpc/"
ACTOR = "whpresspool.bsky.social"
FACTBASE = "https://media-cdn.factba.se/rss/json/trump/calendar-full.json"
HERE = os.path.dirname(os.path.abspath(__file__))
SEEN, LOG = os.path.join(HERE, "seen.json"), os.path.join(HERE, "log.csv")
FIELDS = ["uri", "posted_utc", "seen_utc", "lag_min", "days", "dispatched", "factbase_listed_utc", "kind"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
SCHEDULE_TITLE = re.compile(r"Trump.s (Daily|Weekend) Schedule")
# a free-text update: "the White House has just added an executive order signing at 5PM to the daily guidance"
UPDATE_POST = re.compile(r"\badded\b.{0,160}?\bto the (?:daily |weekend )?(?:guidance|schedule)\b|"
                         r"\b(?:updated|revised) (?:daily |weekend )?(?:guidance|schedule)\b", re.I | re.S)
RANGE_TITLE = re.compile(r"(?:Daily|Weekend) Schedule[^\n]*\n[^\n]*[-\u2013\u2014][^\n]*\d{4}")      # a title line with a dash and a year


def get(url, params=None, timeout=30):
    if params: url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "wh-schedule-watch (read-only)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def mon_num(name):
    n = re.sub(r"[^A-Za-z]", "", name).upper()
    return next((i for i, x in enumerate(MONTHS, 1) if len(n) >= 3 and x.startswith(n[:3])), None)


def days_covered(text, year):
    """dates a schedule post lists: '📅 FRIDAY, NOVEMBER 13' headers, else the title's 'Thursday, Nov. 12, 2026'.
    A title with a date range ('Oct. 13-15') and no per-day headers gives no days (never the first date alone):
    the caller logs it as a warning."""
    out = set()
    lines = [x for x in text.split("\n") if not re.search("[-\u2013\u2014]", x)]       # a line with a dash is a range or a heading, never a day:
    body = "\n".join(lines)                                                      # "Friday, Nov. 13 - Sunday, Nov. 15, 2026" is the real weekend title
    for mon, d in re.findall(r"^\W*(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY),\s+([A-Z]+)\.?\s+(\d{1,2})\b", body.upper(), re.M):
        if mon_num(mon): out.add(dt.date(year, mon_num(mon), int(d)))
    if not out:
        m = re.search(r"(?:day), (\w{3})\w*\.? (\d{1,2}), (\d{4})", body)
        if m and mon_num(m.group(1)): out.add(dt.date(int(m.group(3)), mon_num(m.group(1)), int(m.group(2))))
    return sorted(out)


def range_without_days(text, days):
    """True when the post's title is a date range and no per-day header gave any day"""
    return not days and bool(RANGE_TITLE.search(text))


def kind_of(text):
    """'schedule' (a Daily/Weekend Schedule post), 'update' (an added/updated guidance post), else None"""
    if SCHEDULE_TITLE.search(text[:80]): return "schedule"
    if UPDATE_POST.search(text[:600]): return "update"
    return None


def decide(now_et, kind, days):
    """(job, why) for a new post seen at now_et covering `days` (dates). job is "sched" only for a weekday-evening post that covers
    a later day; None otherwise (the post is only logged, with the reason). Morning and daytime posts for today, same-day
    updates and Sunday evening are logged only: the bot's scheduled pres and refill runs read same-day updates themselves."""
    wd, h, today = now_et.weekday(), now_et.hour, now_et.date()
    later, today_in = any(d > today for d in days), today in days
    if kind == "schedule" and wd <= 4 and 17 <= h <= 23 and later:
        return "sched", "evening: covers a later day"
    if kind == "schedule" and wd == 6 and 17 <= h <= 23 and later:
        return None, "log only: Sunday evening (the bot does not trade Sunday; Monday's 09:07 run reads the schedule itself)"
    if wd <= 4 and 6 <= h < 17 and today_in:
        return None, "log only: " + ("morning same-day release" if h < 9 else
                                      "updated guidance for today" if kind == "update" else "new schedule post covering today")
    return None, "outside every window or covers no relevant day"


def posts():
    """[(uri, createdAt, text, kind)] of the newest schedule and update posts; thread replies of a schedule post joined"""
    feed = get(BSKY + "app.bsky.feed.getAuthorFeed", {"actor": ACTOR, "limit": 30}).get("feed", [])
    out = []
    for it in feed:
        p = it["post"]; txt = p["record"].get("text", "")
        kind = kind_of(txt)
        if not kind: continue
        if kind == "update":
            out.append((p["uri"], p["record"]["createdAt"], txt, kind)); continue
        th = get(BSKY + "app.bsky.feed.getPostThread", {"uri": p["uri"], "depth": 10})["thread"]
        texts, node = [], th
        while node:
            texts.append(node["post"]["record"].get("text", ""))
            node = next((c for c in node.get("replies") or [] if c.get("post", {}).get("author", {}).get("handle") == ACTOR), None)
        out.append((p["uri"], p["record"]["createdAt"], "\n".join(texts), kind))
    return out


def dispatch(job=None):
    tok, repo = os.environ.get("DISPATCH_TOKEN"), os.environ.get("TARGET_REPO")
    if not tok or not repo: return "no target configured"
    wf = os.environ.get("TARGET_WORKFLOW", "live.yml")
    job = (os.environ.get("TARGET_JOB") or "sched") if job in (None, "sched") else job   # TARGET_JOB names the evening job; sched_day and noop keep their names
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches", method="POST",
                                 data=json.dumps({"ref": "main", "inputs": {"job": job}}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return f"dispatched {job} (HTTP {r.status})"
    except Exception as ex:
        return f"dispatch failed: {str(ex)[:120]}"


def main(init=False):
    now = dt.datetime.now(dt.timezone.utc); now_et = now.astimezone(ET)
    seen = json.load(open(SEEN)) if os.path.exists(SEEN) else {"uris": []}
    rows = list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
    new = [x for x in posts() if x[0] not in seen["uris"]]
    for uri, posted, text, kind in sorted(new, key=lambda x: x[1]):
        t_post = dt.datetime.fromisoformat(posted.replace("Z", "+00:00"))
        if kind == "update":
            days = [t_post.astimezone(ET).date()]          # an update is about the day it was made
        else:
            days = days_covered(text, t_post.astimezone(ET).year)
        job, why = decide(now_et, kind, days)
        if init: res = "init"
        elif job: res = dispatch(job)
        else: res = f"not dispatched ({why})"
        if res.startswith("dispatch failed"):        # not seen yet: the next pass retries; five failures in all end it
            fails = seen.setdefault("fails", {}); fails[uri] = fails.get(uri, 0) + 1
            print(f"new {kind} post {posted}: {res} (attempt {fails[uri]})")
            if fails[uri] < 5: continue
            res += "; gave up after 5 attempts"
        if kind == "schedule" and range_without_days(text, days):
            res += "; WARNING date-range title with no per-day headers: no days read"
            print("WARNING: schedule post with a date-range title and no per-day headers; no days read")
        rows.append(dict(uri=uri, posted_utc=t_post.isoformat(timespec="seconds"), seen_utc=now.isoformat(timespec="seconds"),
                         lag_min=round((now - t_post).total_seconds() / 60, 1), days=" ".join(map(str, days)), dispatched=res,
                         factbase_listed_utc="", kind=kind))
        seen["uris"].append(uri)
        seen.get("fails", {}).pop(uri, None)
        print(f"new {kind} post {posted} covering {[str(d) for d in days]}: {res}")
    pending = [r for r in rows if not r["factbase_listed_utc"] and r["days"] and r.get("kind") != "update"]
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
        w = csv.DictWriter(f, fieldnames=FIELDS, restval=""); w.writeheader(); w.writerows(rows)
    if not new: print("no new schedule post")


if __name__ == "__main__":
    if "--test-dispatch" in sys.argv:
        print("test dispatch (job noop):", dispatch("noop"))
    else:
        main(init="--init" in sys.argv)
