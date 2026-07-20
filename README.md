# OnePlus Notes to Google Keep

Migrate notes from a OnePlus/OPlus local Notes backup into a signed-in Google Keep account.

The tool first exports the backup to Markdown and JSON so you can inspect it. It can then open Google Keep in a dedicated Playwright browser profile and create the notes one by one.

> **Important:** This is an unofficial browser-automation tool. Google Keep has no normal consumer bulk-import feature, and Google can change the Keep web interface at any time. Always test with one note before importing everything.

## Features

- Reads OnePlus/OPlus Notes local-backup ZIP files.
- Exports notes to Markdown and JSON before upload.
- Imports titles and note bodies separately into Google Keep.
- Presses **Enter after entering the title**, then re-detects the live body editor.
- Appends the original OnePlus creation and last-updated timestamps to the end of each note.
- Uses a migration state file to avoid importing the same note twice.
- Stops if the text read back from Google Keep is incomplete or different.
- Preserves unlinked files from the OnePlus backup for manual inspection.

## Repository files

```text
.
├── oneplus_to_google_keep.py
├── requirements.txt
├── run_export_windows.bat
├── run_import_windows.bat
├── run_test_one_note_windows.bat
├── .gitignore
├── LICENSE
└── README.md
```

## 1. Create the OnePlus Notes backup

On the OnePlus phone:

1. Open **Settings**.
2. Go to **Additional Settings**.
3. Open **Back up and reset**.
4. Tap **Back up & migrate**.
5. Choose **Local backup**.
6. Select **Notes**.
7. Create the backup.
8. Connect the phone to the computer through USB.
9. Copy the generated backup folder to the computer.

The folder is commonly located under:

```text
Internal Storage/Android/data/com.oneplus.backuprestore/
```

The exact location can differ by OnePlus/OxygenOS version. Copy the complete backup folder, not only individual files inside it.

If the copied backup is a folder rather than a ZIP file, compress the complete backup folder into a ZIP archive before running the script.

## 2. Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- Internet access
- A Google account with Google Keep enabled

The script uses Playwright and opens a Chromium browser. You sign in manually on Google's own sign-in page. The script does not ask for or store your Google password.

## 3. Installation on Windows

Open PowerShell in the repository folder and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

If PowerShell blocks virtual-environment activation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

## 4. Export and inspect the backup first

Do not start with a full Google Keep import. Export the notes locally first:

```powershell
python .\oneplus_to_google_keep.py "C:\path\to\oneplus-notes-backup.zip" `
  --export-dir .\verified_export
```

Review these files:

```text
verified_export/converted_notes.json
verified_export/backup_report.json
verified_export/markdown/
verified_export/unlinked_backup_files/
```

Nothing is uploaded to Google Keep during this export-only step.

## 5. Test the Google Keep import with one note

Use the included helper:

```powershell
.\run_test_one_note_windows.bat "C:\path\to\oneplus-notes-backup.zip"
```

Or run the Python command directly:

```powershell
python .\oneplus_to_google_keep.py "C:\path\to\oneplus-notes-backup.zip" `
  --export-dir .\verified_export `
  --import-to-keep `
  --limit 1
```

The script will:

1. Open Google Keep in a dedicated Chromium profile.
2. Wait for you to sign in manually.
3. Ask for confirmation in PowerShell.
4. Create one test note.
5. Enter the title.
6. Press **Enter** after the title.
7. Re-detect and focus the note-body editor.
8. Paste the body and timestamps.
9. Read the content back and verify it.

Check the test note in Google Keep before proceeding. Confirm that:

- only the actual title appears in the title field;
- the full text appears in the body;
- no text is truncated;
- the timestamps appear at the end.

## 6. Import all remaining notes

After the one-note test succeeds:

```powershell
.\run_import_windows.bat "C:\path\to\oneplus-notes-backup.zip"
```

Or:

```powershell
python .\oneplus_to_google_keep.py "C:\path\to\oneplus-notes-backup.zip" `
  --export-dir .\verified_export `
  --import-to-keep
```

The successful test note is recorded in:

```text
oneplus_keep_import_state.json
```

The full import skips notes already recorded in that file.

## Timestamp format

By default, the script appends the source timestamps in `Asia/Kolkata` time:

```text
— OnePlus note timestamps —
Created: 10 Mar 2021, 03:32:28 PM IST
Last updated: 10 Mar 2021, 03:39:00 PM IST
```

Use another timezone:

```powershell
python .\oneplus_to_google_keep.py "C:\path\to\backup.zip" `
  --import-to-keep `
  --timestamp-timezone UTC
```

Disable timestamps:

```powershell
python .\oneplus_to_google_keep.py "C:\path\to\backup.zip" `
  --import-to-keep `
  --no-source-dates
```

Google Keep does not allow this tool to set the original creation date as Keep's native timestamp. The original dates are therefore preserved as text at the end of the note.

## Sign-in and browser profile

The importer stores its browser session in:

```text
.oneplus_keep_browser_profile/
```

This lets later runs stay signed in. That folder contains authentication cookies and must be treated as sensitive.

After the migration is complete:

1. Close the importer browser.
2. Delete the profile folder:

```powershell
Remove-Item -Recurse -Force .\.oneplus_keep_browser_profile
```

This does not affect your normal Chrome profile.

## Duplicate protection and interrupted imports

Successfully submitted notes are recorded in:

```text
oneplus_keep_import_state.json
```

- Rerunning the importer skips recorded notes.
- If an import stops halfway, fix the issue and run the same command again.
- A failed note is not recorded and will be retried.
- `--force` intentionally imports recorded notes again and can create duplicates.

Do not delete the state file unless you deliberately want to start over.

## Troubleshooting

### The whole note appears in the title

Stop the import immediately and delete the incorrect note from Google Keep. Use the latest script. It enters the title, presses Enter, re-detects the body field, verifies that title and body are different elements, and then pastes the note body.

If an old version recorded the bad note as completed, remove the state file only after deleting the incorrect Google Keep notes:

```powershell
Remove-Item .\oneplus_keep_import_state.json -ErrorAction SilentlyContinue
```

### The script reports 990 characters instead of the full body

This usually means the text went into Keep's title editor or another wrong editable element. Do not keep retrying blindly. Use the latest code, keep the Google Keep interface in English, and test one note again.

### Expected and actual lengths differ by one character

Google Keep can add a trailing newline, non-breaking space, or other invisible terminal character. The script normalizes harmless trailing differences but still stops on meaningful changes or truncation.

### A failed note may already be visible in Keep

Google Keep auto-saves while editing. A partial note may exist even though the script did not record it as imported. Delete that partial note before retrying to avoid duplicates.

### Wrong Google account

Close the importer browser, delete the dedicated browser profile, and run the importer again:

```powershell
Remove-Item -Recurse -Force .\.oneplus_keep_browser_profile
```

### Keep's interface changed

Browser automation is fragile. If Google changes the DOM or labels, the importer may stop safely rather than paste into the wrong field. Open an issue with:

- the exact error message;
- your operating system;
- Python version;
- whether Google Keep is displayed in English;
- a screenshot with private note content hidden.

Do not upload your backup ZIP, browser-profile folder, Google cookies, or private notes to a public issue.

## Privacy and safety

- The backup ZIP can contain private note content. Do not commit it to Git.
- The `.gitignore` excludes ZIP files, export folders, state files, and the browser profile.
- Review the exported Markdown and JSON before upload.
- Keep a separate copy of the original OnePlus backup.
- Test with one note before importing everything.

## Known limitations

- This is not an official Google or OnePlus utility.
- Google Keep's website can change and break automation.
- Rich formatting may be reduced to plain text.
- Native Google Keep creation dates cannot be backdated.
- Checklists, drawings, labels, reminders, colors, and attachments may not migrate completely.
- OnePlus backups may contain files without a reliable mapping to individual notes; such files are exported separately rather than attached incorrectly.

## Command-line help

```powershell
python .\oneplus_to_google_keep.py --help
```

## License

MIT License. See [LICENSE](LICENSE).
