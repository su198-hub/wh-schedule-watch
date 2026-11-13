# wh-schedule-watch

Logs when the President's next-day public schedule appears on the White House press pool relay on Bluesky
(whpresspool.bsky.social), and when Roll Call Factbase's public calendar lists the same day. Optionally starts a
workflow in another repository when a new schedule or same-day update is posted (configured by repository secrets).

When it starts the other workflow (US Eastern time, `decide()` in `watch.py`):

| Window | New post | What happens |
|---|---|---|
| Mon-Fri 17:00-23:59 | a schedule post covering a later day | dispatch (`sched`) |
| Sunday evening; Mon-Fri 06:00-16:59 | a schedule post, or an "added ... to the daily guidance" / "updated guidance" post | logged only (time and days in `log.csv`), nothing dispatched |

A failed dispatch is retried on the next pass (the post is not marked seen until it has been dispatched, or five attempts have failed).
A schedule post whose title is a date range ("Friday, Nov. 13 - Sunday, Nov. 15, 2026", "Oct. 13-15") with no per-day headers gives
no days (never either end of the range) and is logged with a warning. A line with a dash is never read as a day header.

- `watch.py`: one pass, standard library only, public data only. `test_watch.py`: offline tests (`python -m unittest test_watch`).
- `log.csv`: one row per schedule or update post: kind, posted time, first seen by the watcher, days covered, dispatch
  result, first time Factbase listed the day.
- Started by an external scheduler through the workflow-dispatch API (the workflow's own schedule is a best-effort backup).

Not affiliated with the White House, the White House Correspondents' Association, Bluesky or Roll Call.
