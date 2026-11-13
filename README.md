# wh-schedule-watch

Logs when the President's next-day public schedule appears on the White House press pool relay on Bluesky
(whpresspool.bsky.social), and when Roll Call Factbase's public calendar lists the same day. Optionally starts a
workflow in another repository when a new schedule is posted on a weekday evening (configured by repository secrets).

- `watch.py`: one pass, standard library only, public data only.
- `log.csv`: one row per schedule post: posted time, first seen by the watcher, days covered, dispatch result,
  first time Factbase listed the day.
- Runs every 5 minutes in the US evening (`.github/workflows/watch.yml`).

Not affiliated with the White House, the White House Correspondents' Association, Bluesky or Roll Call.
