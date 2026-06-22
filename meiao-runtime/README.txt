MEIAO runtime

Run:
  start-meiao.bat

Open:
  http://127.0.0.1:8787

Portable package:
  From the source workspace, run:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\package-release.ps1 -SkipBuild

  Packages are written to:
    .tmp\packages\meiao-runtime-portable-YYYYMMDD-HHMMSS.zip

  The package is a clean program payload. It excludes user data and runtime leftovers by design:
    storage, config.local.json, logs, drafts, media, integrations/upstream, runtime pid/log files, temp files, and debug backups.

  Update rule:
    replace program files from the package, but preserve the user's storage, config.local.json, logs, drafts, media, and integrations/upstream.

  Rollback rule:
    restore the previous program files, then reuse the preserved user data directories/files.

  Clean install:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install-release.ps1 -PackageZip <package.zip> -TargetRoot <target\meiao-runtime>

  Update:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\update-release.ps1 -PackageZip <package.zip> -TargetRoot <existing\meiao-runtime> -BackupRoot <backup-root>

  Rollback:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rollback-release.ps1 -BackupRoot <backup-root> -TargetRoot <existing\meiao-runtime>

  These scripts are portable maintenance helpers, not a formal installer or auto-updater.

Go runtime launcher:
  The release package includes meiao-runtime.exe.
  start-runtime.ps1 and start-meiao.bat remain supported and delegate to meiao-runtime.exe when it is present.

  Direct commands:
    meiao-runtime.exe start --root .
    meiao-runtime.exe stop --root .
    meiao-runtime.exe restart --root .
    meiao-runtime.exe status --root .
    meiao-runtime.exe health --root .

  Go owns local launch, stop, restart, status, and health supervision only.
  FastAPI remains the main backend, and Python remains the owner of AI, media, Flow, and CapCut logic.

User data:
  By default, data is stored in storage/.
  For packaged handoff, copy config.local.example.json to config.local.json and set dataDir to a local disk or NAS path.
  You can also set MEIAO_DATA_DIR before startup.

Flow Chrome automation:
  Google Chrome must be installed. The runtime auto-detects Chrome from common install paths, Windows registry, and PATH.
  If Chrome is installed in a custom location, set flow.chromePath in config.local.json or set MEIAO_CHROME_PATH/CHROME_PATH before startup.
  Flow uses its own storage/flow-chrome profile copy and remote debugging port 9222, so restarting Flow automation does not close the user's normal Chrome windows.

Included:
  - built frontend
  - local runtime service
  - Go launcher/supervisor
  - embedded Python runtime
  - yt-dlp downloader
  - ffmpeg runtime
  - CapCut Mate integration

Before handoff:
  Run verify-runtime.ps1 from the source workspace.
