# Update And Rollback

Date: 2026-06-14

## Package Model

The portable package contains program files only. User data is intentionally excluded.

Program files can be replaced during an update:

- `meiao-runtime.exe`
- `server.py`
- `meiao_runtime`
- `frontend`
- `python`
- `vendor`
- `runtime`
- `templates`
- `integrations` except `integrations/upstream`
- startup and helper scripts

User data must be preserved:

- `storage`
- `config.local.json`
- `logs`
- `drafts`
- `media`
- `integrations/upstream`

## Safe Update

1. Stop the runtime.
2. Back up the current release directory.
3. Replace program files from the new portable package.
4. Keep user data directories and `config.local.json`.
5. Start the runtime.
6. Verify `http://127.0.0.1:8787/api/health`.

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\update-release.ps1 -PackageZip <package.zip> -TargetRoot <existing\meiao-runtime> -BackupRoot <backup-root>
```

`update-release.ps1` writes the old program payload under `<backup-root>\meiao-runtime` and excludes preserved user data from that backup.

## Git Update Channel

Git updates use a separate checkout/cache and compare the installed `release-manifest.json` with the Git payload manifest. The runtime directory itself must not be a Git repository.

Default cache location:

```text
%LOCALAPPDATA%\Meiao\update-channel\meiao-runtime-update-channel
```

User data remains only in the installed runtime and is not compared with Git:

- `storage`
- `config.local.json`
- `logs`
- `drafts`
- `media`
- `integrations/upstream`

Developer publish command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\publish-git-update.ps1 -SkipBuild
```

Recipient update command, run from the installed `meiao-runtime` directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\update-from-git.ps1
```

The updater fetches `https://github.com/gearaldblynn-lang/meiao-runtime-update-channel.git`, verifies file hashes, compares payload hash/version, backs up replaced program entries, and then applies only manifest-listed program files. Use `-Force` only when the manifest already matches but the program payload must be re-applied.

## Rollback

1. Stop the runtime.
2. Restore the previous program files.
3. Keep the current user data unless a specific data rollback is intentionally required.
4. Start the runtime.
5. Verify `http://127.0.0.1:8787/api/health`.

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rollback-release.ps1 -BackupRoot <backup-root> -TargetRoot <existing\meiao-runtime>
```

Rollback restores program files from `<backup-root>\meiao-runtime` while keeping the current user data in the target runtime.

## Clean Install

Use clean install only for a new empty target directory.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-release.ps1 -PackageZip <package.zip> -TargetRoot <target\meiao-runtime>
```

The target must be empty. The installed runtime starts as a clean program payload without `storage`, `config.local.json`, `logs`, `drafts`, `media`, or `integrations/upstream`.

## Verification

Source/release verification covers the portable maintenance path:

```powershell
python tools\release_install_smoke.py
python tools\release_update_smoke.py
python tools\release_rollback_smoke.py
python tools\git_update_channel_smoke.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-runtime.ps1 -SkipBuild
```

## Safety Rule

Do not package or overwrite real secrets, local configuration, task history, generated media, drafts, runtime logs, or user storage.
