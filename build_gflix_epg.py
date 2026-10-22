#!/usr/bin/env python3
"""Build Gflix EPG's files from one large XMLTV guide.

    python3 Tools/gflix-epg/build_gflix_epg.py [guide] [--bundle] [--previous-source FILE]

The guide is a file, plain XML or gzipped, or a URL to download. With none,
today's EPGSHARE01 all-countries guide is downloaded -- the source Gflix EPG
is built from, re-ripped daily:
https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz

writes, into Tools/gflix-epg/out/ (or --out):

  gflix-epg.xml.gz           the guide the app downloads: every channel that
                             has a programme in the window, and of each
                             programme only what Gflix reads -- start, stop,
                             title, description and its first icon. Duplicate
                             slots (a merged guide often lists one programme
                             from two of its sources) are written once.
  gflix-epg-channels.json    the channel list: id, display names, first icon
                             and how many programmes each has in the guide.
                             The app ships a copy (--bundle copies it into
                             Gflix/Resources/GflixEPG/) so channels are matched
                             and their logos shown before any guide is
                             downloaded, and reads this one again once a day.
  gflix-epg-source.json      what the guide was built from: the source's
                             entity tag, Last-Modified and SHA-256. The app
                             never reads it; the next build does.

Upload them to the latest release of G00380316/gflix-epg under those exact
names; the app asks for

  https://github.com/G00380316/gflix-epg/releases/latest/download/gflix-epg.xml.gz
  https://github.com/G00380316/gflix-epg/releases/latest/download/gflix-epg-channels.json

e.g. `gh release create 2026-10-21 out/* --repo G00380316/gflix-epg --latest`.

--previous-source takes the latest release's gflix-epg-source.json. A source
that hasn't changed since -- its server answers the conditional request with
"not modified", or it hashes the same -- is not built again: nothing is
written, and the script says so and succeeds. Nothing new is published then,
so the app's own conditional request for the guide is answered "not modified"
too, and a phone downloads it only on a day it really changed. A file that
isn't there, as before the first release, means there is nothing to compare.

The window is what the app keeps -- a week and a half of replay, a few days
ahead -- measured from --now (default: now). A guide with less than a quarter
of its programmes inside it is refused, and nothing is written: it is a stale
rip, and would publish a guide with next to nothing in it. --allow-sparse
writes it anyway; --keep-all writes every programme regardless of date.

Only the standard library is used. The guide is read as a stream, element by
element, so a guide of several gigabytes needs no more memory than its channel
list.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BUNDLE_DIR = os.path.join(REPO, "Gflix", "Resources", "GflixEPG")
GUIDE_NAME = "gflix-epg.xml.gz"
CHANNELS_NAME = "gflix-epg-channels.json"
SOURCE_RECORD_NAME = "gflix-epg-source.json"
BUNDLED_CHANNELS_NAME = "GflixEPGChannels.json"
# Long enough for any synopsis the guide shows; a few guides put whole
# articles in <desc>.
DESCRIPTION_LIMIT = 1200
# Below this share of its programmes in the window, a guide is stale.
SPARSE_SHARE = 0.25


def parse_stamp(value):
    """`20260717010000 +0000` (or without the offset, read as UTC)."""
    if not value:
        return None
    value = value.strip()
    try:
        moment = datetime.datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    rest = value[14:].strip()
    offset = datetime.timedelta(0)
    if len(rest) == 5 and rest[0] in "+-" and rest[1:].isdigit():
        offset = datetime.timedelta(hours=int(rest[1:3]), minutes=int(rest[3:5]))
        if rest[0] == "-":
            offset = -offset
    return (moment - offset).replace(tzinfo=datetime.timezone.utc)


def text_of(element):
    return "".join(element.itertext()).strip() if element is not None else ""


def first_icon(element):
    for icon in element.findall("icon"):
        source = (icon.get("src") or "").strip()
        if source:
            return source
    return None


# Where the guide is published, re-ripped every day: every country's
# channels in one file.
DEFAULT_SOURCE = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"


def read_record(path):
    """The source record a release was published with, or None: no file,
    as before the first release, or one this script didn't write."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            record = json.load(file)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("version") == 1 else None


def fetch(url, folder, previous=None):
    """Downloads `url` into `folder`, a megabyte at a time, and says how far.

    Returns the file and its record: what the server called it and what it
    hashes to. Asked conditionally when `previous` is a record of the same
    URL, and a server that answers "not modified" gets (None, previous).
    """
    path = os.path.join(folder, "source-" + os.path.basename(url.split("?")[0]))
    headers = {"User-Agent": "Gflix EPG builder"}
    if previous and previous.get("url") == url:
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("lastModified"):
            headers["If-Modified-Since"] = previous["lastModified"]
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        if error.code == 304 and previous:
            return None, previous
        raise
    digest = hashlib.sha256()
    with response, open(path + ".partial", "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last = 0.0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if time.monotonic() - last > 2:
                last = time.monotonic()
                share = f" of {total / 1e6:.0f}" if total else ""
                print(f"\rdownloading {done / 1e6:.0f}{share} MB", end="", file=sys.stderr, flush=True)
    os.replace(path + ".partial", path)
    print(file=sys.stderr)
    record = {
        "version": 1,
        "url": url,
        "etag": response.headers.get("ETag"),
        "lastModified": response.headers.get("Last-Modified"),
        "sha256": digest.hexdigest(),
        "bytes": done,
    }
    return path, record


def file_record(path):
    """A guide given as a file, recorded as a download is: by what it hashes to."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return {
        "version": 1,
        "url": os.path.basename(path),
        "etag": None,
        "lastModified": None,
        "sha256": digest.hexdigest(),
        "bytes": os.path.getsize(path),
    }


def open_guide(path):
    """The guide as XML, whether the file is it or a gzip of it."""
    with open(path, "rb") as probe:
        gzipped = probe.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rb") if gzipped else open(path, "rb")


def read_guide(path, on_channel, on_programme):
    """Calls back for each <channel> and each <programme>, in document order."""
    source = open_guide(path)
    context = ET.iterparse(source, events=("start", "end"))
    _, root = next(context)
    for event, element in context:
        if event != "end":
            continue
        if element.tag == "channel":
            on_channel(element)
            root.clear()
        elif element.tag == "programme":
            on_programme(element)
            root.clear()
    source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "guide",
        nargs="?",
        default=DEFAULT_SOURCE,
        help="the XMLTV guide to build from: a file, plain or gzipped, or a URL to download "
        f"(default: {DEFAULT_SOURCE})",
    )
    parser.add_argument("--out", default=os.path.join(HERE, "out"))
    parser.add_argument("--now", help="the moment the window is measured from, ISO 8601 (default: now)")
    parser.add_argument("--past-days", type=float, default=8.0)
    parser.add_argument("--future-days", type=float, default=7.0)
    parser.add_argument("--keep-all", action="store_true", help="write every programme, whatever its date")
    parser.add_argument("--keep-download", action="store_true", help="keep a downloaded guide in --out")
    parser.add_argument(
        "--allow-sparse",
        action="store_true",
        help="write the guide even when most of its programmes fall outside the window",
    )
    parser.add_argument("--bundle", action="store_true", help=f"also copy the channel list into {os.path.relpath(BUNDLE_DIR, REPO)}")
    parser.add_argument(
        "--previous-source",
        help=f"the {SOURCE_RECORD_NAME} of the release last published: a source unchanged since is not built "
        "again, and nothing is written (a file that isn't there means none)",
    )
    args = parser.parse_args()

    now = (
        datetime.datetime.fromisoformat(args.now).astimezone(datetime.timezone.utc)
        if args.now
        else datetime.datetime.now(datetime.timezone.utc)
    )
    earliest = now - datetime.timedelta(days=args.past_days)
    latest = now + datetime.timedelta(days=args.future_days)

    # The source first: one unchanged since the last release builds nothing,
    # so nothing new is published and the app's copy stays current.
    os.makedirs(args.out, exist_ok=True)
    previous = read_record(args.previous_source)
    guide = args.guide
    downloaded = None
    if guide.startswith(("http://", "https://")):
        downloaded, record = fetch(guide, args.out, previous)
        # None is the server saying it hasn't changed.
        unchanged = downloaded is None
        guide = downloaded
    else:
        record = file_record(guide)
        unchanged = False
    # A server that ignores the conditional request, or offers nothing to
    # make one with, still sends the same bytes.
    if previous and previous.get("sha256") and previous.get("sha256") == record.get("sha256"):
        unchanged = True
    if unchanged:
        if downloaded and not args.keep_download:
            os.remove(downloaded)
        print(
            f"The source is unchanged since the release last published (SHA-256 {record.get('sha256')}). "
            "Nothing was written."
        )
        return

    # Pass one: channels, and which slots each keeps. Channels come first in
    # XMLTV, but nothing here relies on it.
    channels = {}
    order = []
    counts = {}
    seen_slots = set()
    newest_end = None

    def add_channel(element):
        channel_id = (element.get("id") or "").strip()
        if not channel_id:
            return
        entry = channels.get(channel_id)
        if entry is None:
            entry = {"names": [], "icon": None}
            channels[channel_id] = entry
            order.append(channel_id)
        for name in element.findall("display-name"):
            value = text_of(name)
            if value and value not in entry["names"]:
                entry["names"].append(value)
        if entry["icon"] is None:
            entry["icon"] = first_icon(element)

    guide_path = os.path.join(args.out, GUIDE_NAME)
    partial_path = guide_path + ".partial"
    programme_lines = gzip.open(partial_path + ".body", "wt", encoding="utf-8", compresslevel=9)
    written = 0
    skipped_window = 0
    skipped_duplicate = 0
    # Programmes a day, to say where a stale guide's listings actually are.
    days = {}

    def add_programme(element):
        nonlocal written, skipped_window, skipped_duplicate, newest_end
        channel_id = (element.get("channel") or "").strip()
        start = parse_stamp(element.get("start"))
        stop = parse_stamp(element.get("stop"))
        if not channel_id or start is None or stop is None or stop <= start:
            return
        if newest_end is None or stop > newest_end:
            newest_end = stop
        day = start.date()
        days[day] = days.get(day, 0) + 1
        if not args.keep_all and (stop < earliest or start > latest):
            skipped_window += 1
            return
        slot = (channel_id, int(start.timestamp()))
        if slot in seen_slots:
            skipped_duplicate += 1
            return
        seen_slots.add(slot)
        counts[channel_id] = counts.get(channel_id, 0) + 1

        title = text_of(element.find("title"))
        description = text_of(element.find("desc"))
        if len(description) > DESCRIPTION_LIMIT:
            description = description[: DESCRIPTION_LIMIT - 1].rstrip() + "…"
        icon = first_icon(element)
        parts = [
            f'<programme start="{start.strftime("%Y%m%d%H%M%S")} +0000" '
            f'stop="{stop.strftime("%Y%m%d%H%M%S")} +0000" channel={quoteattr(channel_id)}>'
        ]
        if title:
            parts.append(f"<title>{escape(title)}</title>")
        if description:
            parts.append(f"<desc>{escape(description)}</desc>")
        if icon:
            parts.append(f"<icon src={quoteattr(icon)}/>")
        parts.append("</programme>\n")
        programme_lines.write("".join(parts))
        written += 1

    try:
        read_guide(guide, add_channel, add_programme)
    finally:
        if downloaded and not args.keep_download:
            os.remove(downloaded)
    programme_lines.close()

    # A guide with only a sliver in the window -- a rip left for weeks, its
    # listings all past, a channel or two running ahead -- would publish
    # nothing anyone can watch, and a channel list whose counts say so.
    # Nothing is written, the shipped channel list included.
    read_total = written + skipped_window + skipped_duplicate
    if not args.keep_all and not args.allow_sparse and written < SPARSE_SHARE * read_total:
        os.remove(partial_path + ".body")
        ordered = sorted(days.items())
        busiest = sorted(days, key=days.get, reverse=True)[: max(1, len(days) // 10)]
        listed = (
            f"most of its listings are between {min(busiest):%Y-%m-%d} and {max(busiest):%Y-%m-%d}"
            if ordered
            else "it has no listings"
        )
        sys.exit(
            f"Only {written} of {read_total} programmes fall between {earliest:%Y-%m-%d} and "
            f"{latest:%Y-%m-%d}; {listed}. Nothing was written. Rip the guide again for the "
            f"days around now (--now moves the window, --allow-sparse or --keep-all writes it anyway)."
        )

    # The guide: channels with a programme, then the programmes, recompressed
    # as one gzip member so any reader takes it whole.
    with gzip.open(partial_path, "wt", encoding="utf-8", compresslevel=9) as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(f'<tv generator-info-name="Gflix EPG" date={quoteattr(now.strftime("%Y%m%d%H%M%S +0000"))}>\n')
        for channel_id in order:
            if not counts.get(channel_id):
                continue
            entry = channels[channel_id]
            out.write(f"<channel id={quoteattr(channel_id)}>")
            for name in entry["names"]:
                out.write(f"<display-name>{escape(name)}</display-name>")
            if entry["icon"]:
                out.write(f"<icon src={quoteattr(entry['icon'])}/>")
            out.write("</channel>\n")
        with gzip.open(partial_path + ".body", "rt", encoding="utf-8") as body:
            shutil.copyfileobj(body, out, 1 << 20)
        out.write("</tv>\n")
    os.remove(partial_path + ".body")
    os.replace(partial_path, guide_path)

    # The channel list keeps every channel, guide or not: its icon is still a
    # logo. Arrays rather than objects keep it small enough to ship.
    listing = {
        "version": 1,
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channels": [
            [channel_id, channels[channel_id]["names"], channels[channel_id]["icon"], counts.get(channel_id, 0)]
            for channel_id in order
        ],
    }
    channels_path = os.path.join(args.out, CHANNELS_NAME)
    with open(channels_path, "w", encoding="utf-8") as out:
        json.dump(listing, out, ensure_ascii=False, separators=(",", ":"))

    # Last, so a record is only ever beside a guide it was built into.
    record_path = os.path.join(args.out, SOURCE_RECORD_NAME)
    with open(record_path, "w", encoding="utf-8") as out:
        json.dump(record, out, indent=1)

    if args.bundle:
        os.makedirs(BUNDLE_DIR, exist_ok=True)
        shutil.copyfile(channels_path, os.path.join(BUNDLE_DIR, BUNDLED_CHANNELS_NAME))

    with_guide = sum(1 for channel_id in order if counts.get(channel_id))
    print(f"{len(order)} channels, {with_guide} with a guide; {written} programmes written")
    print(f"skipped {skipped_window} outside the window, {skipped_duplicate} duplicate slots")
    print(f"{guide_path}: {os.path.getsize(guide_path) / 1e6:.1f} MB")
    print(f"{channels_path}: {os.path.getsize(channels_path) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
