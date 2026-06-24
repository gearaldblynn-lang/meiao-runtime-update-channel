# Meiao Runtime Update Channel

This repository is a program update channel only. It must not contain local runtime data.

Runtime user data stays on each user's machine and is preserved by update-from-git.ps1:

- storage
- config.local.json
- logs
- drafts
- media
- integrations/upstream
- integrations/capcut_mate/upstream/capcut-mate-main/output
- integrations/capcut_mate/upstream/capcut-mate-main/logs
- integrations/capcut_mate/upstream/capcut-mate-main/db
- integrations/capcut_mate/upstream/capcut-mate-main/temp

Publish from the development machine with publish-git-update.ps1. Update an installed runtime with update-from-git.ps1 from the runtime directory.