# LLM Gateway Status

Public status page for the LLM Gateway, served by GitHub Pages.

- Page: <https://michael3abc.github.io/llm-gateway-status/>
- Overall status: **operational / degraded / down** (or "no report yet" before the first update)
- 90-day history as one bar per UTC day (worst status of that day), plus an incident log
- Static HTML only: no analytics, no cookies, no third-party scripts. Light/dark follows the OS setting and the layout works on phones.

## How it works

| File | Purpose |
| --- | --- |
| `index.html` | The whole page (inline CSS/JS, reads the JSON files below) |
| `data/status.json` | Current status: `status`, `updated_at`, `message` |
| `data/history.json` | `days`: map of `YYYY-MM-DD` to `{status, note?}`; days with no entry show as "no data" (nothing is back-filled) |
| `data/incidents.json` | `incidents`: list of events `{id, time, status, title, message?}` |
| `scripts/update_status.py` | Update interface: edits the JSON, commits, pushes |
| `.github/workflows/pages.yml` | On every push to `main`: validate the JSON and deploy to Pages |

## Updating the status

Run the script on any machine with a clone of this repo and push access (Python 3, standard library only):

```sh
# Report a problem (adds an incident entry, sets the banner, marks today)
scripts/update_status.py --status degraded \
    --title "Elevated latency" \
    --message "Upstream provider is slow; investigating." \
    --trailer "Paperclip-Issue: HOM-503"

# Report recovery
scripts/update_status.py --status operational --title "Resolved" \
    --message "Latency is back to normal."

# Heartbeat: refresh the "last updated" time and today's bar, no incident entry
scripts/update_status.py --status operational
```

Options:

- `--status {operational,degraded,down}` (required)
- `--title`, `--message`: with `--title` an event is appended to `data/incidents.json`; without it the update is a heartbeat
- `--trailer "Key: value"`: add a commit trailer (repeatable)
- `--time ISO8601`: record a specific time instead of now (UTC)
- `--dry-run`: show the resulting `status.json` / `incidents.json` without touching anything
- `--no-commit`, `--no-pull`, `--no-push`: stop earlier in the pipeline

The script takes a lock, runs `git pull --rebase`, writes the three JSON files atomically, commits only `data/`, and pushes. The push triggers the **Deploy Pages** workflow; the page is usually updated about a minute later. If today already has a worse status, the day keeps the worse one (it only ever goes up within a UTC day). Exit codes: `0` ok, `1` error, `2` another update is running.

### Calling it from the gateway

Call the script from the gateway's scheduler or alerting hook, for example on a status *change* (heartbeats make a commit each time, so keep them to roughly hourly at most). The page shows a "may be stale" note when the last update is over 24 hours old.

```sh
/path/to/llm-gateway-status/scripts/update_status.py --status down \
    --title "Gateway unreachable" --message "Health check failed 3 times."
```

Plain `git push` of hand-edited JSON also works; the workflow refuses to deploy invalid JSON.

## Local preview

```sh
python3 -m http.server -d . 8000   # then open http://localhost:8000/
```

## Notes

- The history starts on the day of the first update; earlier days are intentionally empty.
- The uptime figure on the page is "share of reported days that were fully operational", not a minute-level SLA.
- Incident text is rendered as plain text, never as HTML.
