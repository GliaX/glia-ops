# glia-ops

Operational health checks for the Glia ERPNext site, run by the Frappe
scheduler.

## What it watches

**Backups.** The backup CronJob (`helm-erpnext/backup/`) writes
`sites/<site>/private/backups/last_run.json` on every run (status, stage, stamp,
finish time). Nothing watched that file, so a silent failure would only surface
when a restore was needed. The daily check emails **info@glia.org** when the
marker is:

- missing (no backup has ever completed, or the volume was replaced),
- `failed` (it reports the stage that failed), or
- older than 26 h (the schedule is every 6 h, so three missed runs).

Alerts are de-duplicated for 12 h, and the dedupe clears automatically once a
healthy backup lands, so the next failure alerts immediately.

Check manually:

```sh
bench --site asset.glia.org execute glia_ops.backups.run_check
# or, as an admin, GET /api/method/glia_ops.backups.run_check
```

## Install

```sh
bench get-app https://github.com/GliaX/glia-ops --branch v0.1.0
bench --site <site> install-app glia_ops
```

The scheduler must be enabled (it is: the `erpnext-scheduler` pod runs).

## Why not a Server Script?

Frappe's Server Script scheduler events run in RestrictedPython, which forbids
underscore-prefixed helpers and file access patterns this check needs, and
enabling Server Scripts globally widens the site's scripting surface for no
benefit. A tiny app keeps the logic reviewable in git and needs no config flag.

## Licence

MIT.
