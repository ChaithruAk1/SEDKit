# Where SED keeps its data

Everything that is not code lives in a **data root** outside the repo:
- one folder per profile (`synthetic`, `real`, ...), holding the database, inbox, AI runs, outputs, backups, local
  config overrides and the PII salt;
- machine-level files: `agent.yaml`, `guard\denylist.txt`, and the `profiles.json` and `claude_managed.json` records.

The data root is `SED_DATA_ROOT` when that environment variable is set, otherwise `%LOCALAPPDATA%\sed`.

## The Claude desktop app redirects %LOCALAPPDATA%

The Claude desktop app for Windows is a packaged (MSIX) app. Windows gives such apps a private copy of
`%LOCALAPPDATA%`. Folders that the app creates there, or that any program it starts creates (including `uv run sed`
in a Claude session), really live in `%LOCALAPPDATA%\Packages\<package>\LocalCache\Local`. For SED's default location
that means:
- **Other programs can't see the data.** Your own terminal, Explorer and Task Scheduler see a different, empty
  `%LOCALAPPDATA%\sed`.
- **Removing or resetting the app deletes it.** That includes the databases, backups and the PII salt. Without the
  salt, new real-data imports get different pseudonyms.

`uv run sed doctor` reports this as `data_dir_not_in_app_storage`: a warning for synthetic profiles, a failure for the
real profile.

## Fix: a data root in your user profile

Use a folder inside your user profile, for example `C:\Users\<you>\SEDData`. Your profile folder is private to your
account, OneDrive does not sync it, and no app redirects it. Avoid a folder directly under `C:\`: it inherits
permissions that let every signed-in account read and change it.

1. Stop `sed serve` if it is running.
2. Move the data (add `--dry-run` first to see what would be copied):
   ```powershell
   uv run sed data move --to "$env:USERPROFILE\SEDData"
   ```
   This copies every profile and verifies each database (schema, meta, row counts) and salt against the original. It
   points `.claude/settings.local.json` at the new folders and marks the old root as moved. Nothing is deleted.
3. Set the variable for your Windows account: Start > **Edit environment variables for your account** > New, name
   `SED_DATA_ROOT`, value the new folder. Use this Windows dialog rather than a terminal inside the Claude app, because
   the app may keep changes made from inside it private too.
4. Restart the Claude desktop app and any open terminals so they pick up the variable.
5. Check that `uv run sed doctor --profile <profile>` shows `data_dir_not_in_app_storage` as ok.
6. When you are happy, delete the old folder. The move prints its real location.

Until a program sees the variable, sed refuses to use the old root and says where the data went. That way nothing is
written to the stale copy.

## Changing it later

Run the move again from the current root (`uv run sed data move --to <new folder>`), update `SED_DATA_ROOT`, and
restart. Going back to `%LOCALAPPDATA%\sed` without the variable only works on a machine where you don't run SED from
the Claude desktop app.

## Other machines

Before `sed init --profile real` on another machine, decide the data root first. If you will run SED from the Claude
desktop app there, set `SED_DATA_ROOT` before `init`. The real profile and its salt are then created in the right place
from the start. Scheduled tasks read the same account variable.
