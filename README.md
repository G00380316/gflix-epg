# Gflix EPG

The TV guide Gflix offers as **Gflix EPG** (Edit Source › EPG Sources), built
every day from [EPGSHARE01](https://epgshare01.online)'s all-countries guide.

The latest release holds the two files the app reads:

- `gflix-epg.xml.gz` — the guide, trimmed to the days the app keeps.
- `gflix-epg-channels.json` — every channel's id, names, logo and programme count.

`.github/workflows/build.yml` runs `build_gflix_epg.py` each morning and
publishes a release; a day the source didn't update publishes nothing. Run it
by hand from the Actions tab, or locally:

```bash
python3 build_gflix_epg.py --out out
```

The guide is EPGSHARE01's work, rehosted so the app's downloads don't land on
their donation-funded server. Support them at https://ko-fi.com/W7W0YGC4T.
