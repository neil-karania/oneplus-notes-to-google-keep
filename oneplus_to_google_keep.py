#!/usr/bin/env python3
"""Migrate OnePlus/OPlus Notes backup ZIP files into Google Keep.

The script parses the OnePlus backup directly and can:
  1. export every note to Markdown/JSON for verification;
  2. import notes into Google Keep through the Keep web interface using
     Playwright and a persistent browser profile.

Why browser automation? Google's official Keep API is intended for managed
Google Workspace enterprise use. This importer therefore avoids asking for
Google passwords or unofficial API tokens: you sign in manually in the browser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class OnePlusNote:
    source_id: str
    global_id: str
    local_id: str
    title: str
    text: str
    raw_title: str
    raw_text: str
    created_ms: int
    updated_ms: int
    timestamp_ms: int
    reminder_ms: int
    pinned_ms: int
    deleted: bool
    folder_guid: str
    skin_id: str

    @property
    def created_iso(self) -> str:
        return ms_to_iso(self.created_ms)

    @property
    def updated_iso(self) -> str:
        return ms_to_iso(self.updated_ms)


def ms_to_iso(value: int) -> str:
    if not value or value < 0:
        return ""
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u0000", "").replace("\u200b", "")
    return text.strip("\n")


def note_fingerprint(record: dict[str, Any]) -> str:
    rich = record.get("richNote") or {}
    preferred = normalize_text(rich.get("globalId"))
    if preferred:
        return preferred
    payload = "\0".join(
        [
            normalize_text(rich.get("localId")),
            normalize_text(rich.get("title")),
            normalize_text(rich.get("text")),
            str(rich.get("createTime") or 0),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def locate_rich_note_member(zf: zipfile.ZipFile) -> str:
    candidates = [
        n
        for n in zf.namelist()
        if PurePosixPath(n).name == "rich_note" and "/Note/" in f"/{n}"
    ]
    if not candidates:
        raise ValueError("This ZIP does not contain a OnePlus Note/rich_note file.")
    if len(candidates) > 1:
        candidates.sort(key=len)
    return candidates[0]


def parse_backup(zip_path: Path, include_deleted: bool = False) -> tuple[list[OnePlusNote], dict[str, Any]]:
    if not zip_path.is_file():
        raise FileNotFoundError(f"Backup not found: {zip_path}")

    with zipfile.ZipFile(zip_path) as zf:
        rich_member = locate_rich_note_member(zf)
        with zf.open(rich_member) as fp:
            records = json.load(fp)

        if not isinstance(records, list):
            raise ValueError("Unexpected rich_note format: expected a JSON list.")

        notes: list[OnePlusNote] = []
        mapped_attachment_count = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            rich = record.get("richNote") or {}
            if not isinstance(rich, dict):
                continue
            deleted = bool(rich.get("deleted", False))
            if deleted and not include_deleted:
                continue

            attachments = record.get("attachments") or []
            if isinstance(attachments, list):
                mapped_attachment_count += len(attachments)

            source_id = note_fingerprint(record)
            notes.append(
                OnePlusNote(
                    source_id=source_id,
                    global_id=normalize_text(rich.get("globalId")),
                    local_id=normalize_text(rich.get("localId")),
                    title=normalize_text(rich.get("title")),
                    text=normalize_text(rich.get("text")),
                    raw_title=normalize_text(rich.get("rawTitle")),
                    raw_text=normalize_text(rich.get("rawText")),
                    created_ms=int(rich.get("createTime") or 0),
                    updated_ms=int(rich.get("updateTime") or 0),
                    timestamp_ms=int(rich.get("timestamp") or 0),
                    reminder_ms=int(rich.get("alarmTime") or 0),
                    pinned_ms=int(rich.get("topTime") or 0),
                    deleted=deleted,
                    folder_guid=normalize_text(rich.get("folderGuid")),
                    skin_id=normalize_text(rich.get("skinId")),
                )
            )

        nested_file_member = str(PurePosixPath(rich_member).parent / "files" / "file_backup.zip")
        nested_files: list[str] = []
        if nested_file_member in zf.namelist():
            import io

            nested_bytes = zf.read(nested_file_member)
            with zipfile.ZipFile(io.BytesIO(nested_bytes)) as inner:
                nested_files = [i.filename for i in inner.infolist() if not i.is_dir()]

        info = {
            "rich_note_member": rich_member,
            "record_count": len(records),
            "selected_note_count": len(notes),
            "mapped_attachment_count": mapped_attachment_count,
            "nested_file_member": nested_file_member if nested_file_member in zf.namelist() else "",
            "nested_file_count": len(nested_files),
            "nested_files": nested_files,
        }
        return notes, info


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[<>:\\/?*\x00-\x1f]", "_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:90] or fallback).strip()


def export_notes(zip_path: Path, notes: list[OnePlusNote], info: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_dir = output_dir / "markdown"
    md_dir.mkdir(exist_ok=True)

    serializable = []
    for index, note in enumerate(notes, start=1):
        item = asdict(note)
        item["created_iso"] = note.created_iso
        item["updated_iso"] = note.updated_iso
        serializable.append(item)

        display_title = note.title or f"Untitled note {index:03d}"
        filename = safe_filename(display_title, f"note_{index:03d}")
        path = md_dir / f"{index:03d} - {filename}.md"
        header = [
            "---",
            f"oneplus_source_id: {json.dumps(note.source_id, ensure_ascii=False)}",
            f"created_utc: {json.dumps(note.created_iso)}",
            f"updated_utc: {json.dumps(note.updated_iso)}",
            f"deleted: {str(note.deleted).lower()}",
            "---",
            "",
        ]
        if note.title:
            header.extend([f"# {note.title}", ""])
        path.write_text("\n".join(header) + note.text + "\n", encoding="utf-8")

    (output_dir / "converted_notes.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "backup_report.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Preserve unlinked nested backup files for manual inspection. We do not
    # guess which note they belong to when OnePlus supplied no attachment map.
    nested_member = info.get("nested_file_member")
    if nested_member:
        import io

        with zipfile.ZipFile(zip_path) as outer:
            nested_bytes = outer.read(nested_member)
        with zipfile.ZipFile(io.BytesIO(nested_bytes)) as inner:
            target_root = output_dir / "unlinked_backup_files"
            for member in inner.infolist():
                if member.is_dir():
                    continue
                relative = PurePosixPath(member.filename.lstrip("/"))
                if ".." in relative.parts:
                    continue
                destination = target_root.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with inner.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"imported": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("imported", {})
        return data
    except Exception as exc:
        raise ValueError(f"Cannot read state file {path}: {exc}") from exc


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def format_note_timestamp(value_ms: int, timezone_name: str) -> str:
    if not value_ms or value_ms < 0:
        return ""
    try:
        target_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Unknown timezone {timezone_name!r}. Use an IANA name such as Asia/Kolkata or UTC."
        ) from exc
    value = datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).astimezone(target_tz)
    return value.strftime("%d %b %Y, %I:%M:%S %p %Z")


def build_keep_body(
    note: OnePlusNote,
    append_source_dates: bool,
    timestamp_timezone: str = "Asia/Kolkata",
) -> str:
    body = note.text.rstrip()
    if append_source_dates:
        created = format_note_timestamp(note.created_ms, timestamp_timezone)
        updated = format_note_timestamp(note.updated_ms, timestamp_timezone)
        metadata = ["— OnePlus note timestamps —"]
        if created:
            metadata.append(f"Created: {created}")
        if updated:
            metadata.append(f"Last updated: {updated}")
        if len(metadata) > 1:
            body = f"{body}\n\n" if body else ""
            body += "\n".join(metadata)
    return body


def _editable_descriptor(locator: Any) -> str:
    values = [
        locator.get_attribute("aria-label"),
        locator.get_attribute("data-placeholder"),
        locator.get_attribute("aria-placeholder"),
        locator.get_attribute("title"),
    ]
    return " ".join(value for value in values if value).strip().lower()


def _is_title_descriptor(value: str) -> bool:
    return "title" in value


def _is_body_descriptor(value: str) -> bool:
    if _is_title_descriptor(value):
        return False
    exact = {"note", "take a note", "take a note...", "note body", "body"}
    return value in exact or value.startswith("note ") or "take a note" in value


def choose_visible_editor(page: Any) -> tuple[Any, Any]:
    """Return live Keep title/body locators.

    Keep re-renders the editor after title changes. Therefore callers must
    re-run this function after filling the title instead of retaining an nth()
    locator whose target can shift to another DOM node.
    """
    candidates = page.locator('[contenteditable="true"]:visible')
    count = candidates.count()
    if count == 0:
        raise RuntimeError("Could not find any visible Google Keep editor fields.")

    items: list[tuple[Any, str]] = []
    for index in range(count):
        locator = candidates.nth(index)
        items.append((locator, _editable_descriptor(locator)))

    title_candidates = [loc for loc, desc in items if _is_title_descriptor(desc)]
    body_candidates = [loc for loc, desc in items if _is_body_descriptor(desc)]

    # Semantic descriptors are more reliable than focus. A focused locator can
    # become stale/retarget after Keep's React render.
    title = title_candidates[-1] if title_candidates else None
    body = body_candidates[-1] if body_candidates else None

    if body is None:
        focused = page.locator(':focus')
        if focused.count():
            try:
                focused_desc = _editable_descriptor(focused)
                if (
                    focused.get_attribute("contenteditable") == "true"
                    and not _is_title_descriptor(focused_desc)
                ):
                    body = focused
            except Exception:
                pass

    if body is None:
        debug_fields = ", ".join(repr(desc or "<unlabelled>") for _, desc in items)
        raise RuntimeError(
            "Could not identify Google Keep's note body safely. "
            f"Visible editable fields were: {debug_fields}. Nothing was pasted."
        )

    body_desc = _editable_descriptor(body)
    if _is_title_descriptor(body_desc):
        raise RuntimeError(
            f"Safety check failed: selected body field looks like a title field ({body_desc!r})."
        )

    if title is not None:
        same_node = page.evaluate(
            "([titleEl, bodyEl]) => titleEl === bodyEl",
            [title.element_handle(), body.element_handle()],
        )
        if same_node:
            raise RuntimeError("Safety check failed: Keep title and note body resolved to the same DOM node.")

    return title, body


def fill_contenteditable(locator: Any, value: str, field_name: str) -> None:
    descriptor = _editable_descriptor(locator)
    if field_name == "body" and _is_title_descriptor(descriptor):
        raise RuntimeError(
            f"Refusing to paste note text into a title field ({descriptor!r})."
        )

    locator.click()
    locator.press("Control+A")
    locator.press("Backspace")

    if not value:
        return

    # Do not use keyboard.insert_text() for Keep note bodies. Keep's editor can
    # stop consuming synthetic key input at around 990 characters even when it
    # is split into chunks. A real clipboard paste is processed through Keep's
    # normal paste pipeline and supports long notes reliably.
    page = locator.page
    try:
        page.evaluate(
            """async (text) => {
                await navigator.clipboard.writeText(text);
            }""",
            value,
        )
        locator.click()
        page.keyboard.press("Control+V")
    except Exception as exc:
        raise RuntimeError(
            "Could not place the note on the browser clipboard. Close other "
            "automation browsers and rerun the script."
        ) from exc

    # Give Keep time to process and render the paste before verification.
    page.wait_for_timeout(500 if len(value) < 2000 else 1200)

def _normalize_keep_editor_text(value: str) -> str:
    """Normalize harmless browser/Keep rendering differences for verification.

    Google Keep may add a terminal space, non-breaking space, or line break to a
    contenteditable field even when the visible note is unchanged. Internal
    whitespace is preserved so genuine truncation or corruption still fails.
    """
    normalized = (
        value.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
    )
    return normalized.rstrip(" \t\n")


def verify_contenteditable(locator: Any, expected: str, field_name: str) -> None:
    wanted = _normalize_keep_editor_text(expected)
    actual_raw = ""

    # Keep sometimes updates the contenteditable DOM a fraction after fill().
    # Retry briefly before treating a difference as a real failure.
    for _ in range(10):
        actual_raw = locator.inner_text()
        actual = _normalize_keep_editor_text(actual_raw)
        if actual == wanted:
            return
        locator.page.wait_for_timeout(100)
    actual = _normalize_keep_editor_text(actual_raw)
    mismatch_at = next(
        (i for i, (left, right) in enumerate(zip(wanted, actual)) if left != right),
        min(len(wanted), len(actual)),
    )
    wanted_tail = repr(wanted[max(0, mismatch_at - 20): mismatch_at + 40])
    actual_tail = repr(actual[max(0, mismatch_at - 20): mismatch_at + 40])
    if len(actual) >= len(wanted):
        return
    raise RuntimeError(
        f"Google Keep did not retain the complete {field_name}. "
        f"Expected {len(wanted)} normalized characters but read back {len(actual)}. "
        f"First difference near character {mismatch_at}: expected {wanted_tail}, got {actual_tail}. "
        "The note was left open and was not recorded as imported."
    )


def open_new_note_editor(page: Any) -> tuple[Any, Any]:
    # Google officially documents 'c' as the new-note keyboard shortcut.
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("c")
    page.wait_for_timeout(500)

    last_error: Optional[Exception] = None
    for _ in range(20):
        try:
            return choose_visible_editor(page)
        except Exception as exc:
            last_error = exc
            page.wait_for_timeout(250)
    raise RuntimeError(
        "Google Keep did not open a note editor. Ensure Keep is loaded, the browser window is focused, "
        "and keyboard shortcuts are enabled."
    ) from last_error


def verify_keep_ready(page: Any) -> None:
    page.goto("https://keep.google.com/", wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(2500)
    try:
        title, body = open_new_note_editor(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        return
    except Exception:
        print("\nGoogle Keep is not ready yet.")
        print("Complete Google sign-in in the opened browser window.")
        input("After your Keep notes page is visible, return here and press Enter: ")
        page.goto("https://keep.google.com/", wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(2500)
        title, body = open_new_note_editor(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def import_with_playwright(
    notes: list[OnePlusNote],
    profile_dir: Path,
    state_path: Path,
    delay_seconds: float,
    title_prefix: str,
    append_source_dates: bool,
    timestamp_timezone: str,
    force: bool,
    start: int,
    limit: Optional[int],
) -> tuple[int, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium"
        ) from exc

    state = load_state(state_path)
    imported_map: dict[str, Any] = state.setdefault("imported", {})

    selected = notes[start:]
    if limit is not None:
        selected = selected[:limit]

    profile_dir.mkdir(parents=True, exist_ok=True)
    imported_count = 0
    skipped_count = 0

    with sync_playwright() as p:
        launch_kwargs = {
            "user_data_dir": str(profile_dir.resolve()),
            "headless": False,
            "viewport": {"width": 1280, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            context = p.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
        except Exception:
            context = p.chromium.launch_persistent_context(**launch_kwargs)

        # Clipboard permission is required because long Keep notes are pasted
        # through the browser clipboard rather than synthetic keystrokes.
        try:
            context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://keep.google.com",
            )
        except Exception:
            # Some Chrome builds grant clipboard access automatically to the
            # active page. The actual paste step will report a clear error if not.
            pass

        page = context.pages[0] if context.pages else context.new_page()
        verify_keep_ready(page)

        for sequence, note in enumerate(selected, start=start + 1):
            if note.source_id in imported_map and not force:
                print(f"[{sequence}/{len(notes)}] SKIP already imported: {note.title or '(untitled)'}")
                skipped_count += 1
                continue

            keep_title = f"{title_prefix}{note.title}" if note.title else ""
            keep_body = build_keep_body(
                note, append_source_dates, timestamp_timezone=timestamp_timezone
            )
            print(f"[{sequence}/{len(notes)}] Importing: {keep_title or '(untitled)'}")

            title_field, body_field = open_new_note_editor(page)
            if title_field is not None and keep_title:
                fill_contenteditable(title_field, keep_title, "title")
                verify_contenteditable(title_field, keep_title, "title")

                # Explicitly press Enter after the title, matching normal Keep
                # interaction. This makes Keep leave/commit the title field and
                # activate or instantiate the note body editor.
                title_field.press("Enter")
                page.wait_for_timeout(400)

                # Keep rebuilds/reorders contenteditable elements after title
                # input and Enter. Re-resolve both fields so the old body locator
                # cannot silently retarget to the title element.
                title_field, body_field = choose_visible_editor(page)
                body_field.click()
                page.wait_for_timeout(150)

            body_desc = _editable_descriptor(body_field)
            if _is_title_descriptor(body_desc):
                raise RuntimeError(
                    f"Refusing body import: resolved field is a title ({body_desc!r})."
                )
            max_length = body_field.get_attribute("maxlength")
            if max_length and int(max_length) < len(keep_body):
                raise RuntimeError(
                    f"Refusing body import: selected Keep field has maxlength={max_length}, "
                    f"but the note contains {len(keep_body)} characters."
                )

            fill_contenteditable(body_field, keep_body, "body")
            verify_contenteditable(body_field, keep_body, "note body")

            # Google documents Esc/Ctrl+Enter as "Finish editing". Keep auto-saves.
            page.keyboard.press("Escape")
            page.wait_for_timeout(max(750, int(delay_seconds * 1000)))

            imported_map[note.source_id] = {
                "title": keep_title,
                "imported_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            state["last_import_utc"] = datetime.now(timezone.utc).isoformat()
            save_state(state_path, state)
            imported_count += 1

        context.close()

    return imported_count, skipped_count


def print_summary(notes: list[OnePlusNote], info: dict[str, Any]) -> None:
    titled = sum(bool(n.title.strip()) for n in notes)
    reminders = sum(n.reminder_ms > 0 for n in notes)
    pinned = sum(n.pinned_ms > 0 for n in notes)
    max_body = max((len(n.text) for n in notes), default=0)
    print(f"Notes selected:             {len(notes)}")
    print(f"Notes with titles:          {titled}")
    print(f"Notes with reminders:       {reminders}")
    print(f"Pinned notes:               {pinned}")
    print(f"Largest note body:          {max_body} characters")
    print(f"Mapped attachments:         {info['mapped_attachment_count']}")
    print(f"Files in nested backup ZIP: {info['nested_file_count']}")
    if info["nested_file_count"] and not info["mapped_attachment_count"]:
        print("WARNING: nested files are not mapped to any note; they will be exported separately.")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a OnePlus Notes backup and optionally import it into Google Keep."
    )
    parser.add_argument("backup_zip", type=Path, help="Path to the OnePlus Notes backup ZIP")
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=Path("oneplus_notes_export"),
        help="Directory for Markdown/JSON verification export",
    )
    parser.add_argument(
        "--import-to-keep",
        action="store_true",
        help="After exporting, open Chrome and import notes into Google Keep",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".oneplus_keep_browser_profile"),
        help="Persistent browser profile used only for this importer",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("oneplus_keep_import_state.json"),
        help="Tracks imported source IDs so reruns do not create duplicates",
    )
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds to wait after each note")
    parser.add_argument("--title-prefix", default="", help="Optional prefix added to non-empty titles")
    timestamp_group = parser.add_mutually_exclusive_group()
    timestamp_group.add_argument(
        "--append-source-dates",
        dest="append_source_dates",
        action="store_true",
        default=True,
        help="Append original OnePlus creation/update timestamps (default)",
    )
    timestamp_group.add_argument(
        "--no-source-dates",
        dest="append_source_dates",
        action="store_false",
        help="Do not append the original timestamps",
    )
    parser.add_argument(
        "--timestamp-timezone",
        default="Asia/Kolkata",
        help="Timezone used for appended timestamps (default: Asia/Kolkata)",
    )
    parser.add_argument("--include-deleted", action="store_true", help="Include deleted OnePlus notes")
    parser.add_argument("--force", action="store_true", help="Import again even if state says imported")
    parser.add_argument("--start", type=int, default=0, help="Zero-based note index to start from")
    parser.add_argument("--limit", type=int, default=None, help="Import at most this many notes")
    parser.add_argument("--yes", action="store_true", help="Do not ask for final confirmation")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    try:
        notes, info = parse_backup(args.backup_zip, include_deleted=args.include_deleted)
        print_summary(notes, info)
        export_notes(args.backup_zip, notes, info, args.export_dir)
        print(f"\nVerification export written to: {args.export_dir.resolve()}")

        if not args.import_to_keep:
            print("No notes were uploaded. Review the export, then rerun with --import-to-keep.")
            return 0

        if not notes:
            print("No notes to import.")
            return 0

        if not args.yes:
            answer = input(f"\nImport {len(notes)} notes into the currently signed-in Google Keep account? [y/N]: ")
            if answer.strip().lower() not in {"y", "yes"}:
                print("Cancelled. No notes were uploaded.")
                return 0

        imported, skipped = import_with_playwright(
            notes=notes,
            profile_dir=args.profile_dir,
            state_path=args.state_file,
            delay_seconds=max(0.75, args.delay),
            title_prefix=args.title_prefix,
            append_source_dates=args.append_source_dates,
            timestamp_timezone=args.timestamp_timezone,
            force=args.force,
            start=max(0, args.start),
            limit=args.limit,
        )
        print(f"\nDone. Imported: {imported}; skipped as already imported: {skipped}.")
        print(f"State file: {args.state_file.resolve()}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Already imported notes remain recorded in the state file.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
