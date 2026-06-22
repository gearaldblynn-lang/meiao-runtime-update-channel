# capcut-mate integration

This folder is the local boundary for the CapCut/Jianying draft engine.

## Ownership

MEIAO owns:

- task model
- draft plan
- scene/material matching
- local media URLs
- usage limits and QC status

capcut-mate owns:

- creating Jianying draft files
- adding video/audio/caption tracks
- saving the draft

## Runtime shape

The recommended first version keeps capcut-mate as a local sidecar service:

```text
MEIAO runtime : http://127.0.0.1:8787
capcut-mate  : http://127.0.0.1:30000
```

MEIAO should pass `http://127.0.0.1:8787/media/...` scene clip URLs to capcut-mate, not temporary third-party URLs.

## Upstream source

When the GitHub source can be pulled on this machine, place the upstream repository under:

```text
integrations/capcut_mate/upstream/
```

Keep `adapter.py` as the stable internal adapter. Application code should call the adapter, not upstream modules directly.
