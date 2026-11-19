# skjerm

The info screen in the lobby at Gründerhuset Hi5, Tønsberg. It shows upcoming events from Hi5's public Google Calendar on a Samsung Flip touchscreen.

Live page: <https://grunderhuset-hi5.github.io/skjerm/>. The Flip's built-in browser is pointed at this URL and left running.

This README explains what the screen is for, how it works, and why it was built the way it was, so someone new can take it over without the original conversation.

## Intent

- **The calendar is the only control panel.** Hi5 staff edit events in Google Calendar and the screen follows. Nobody should need to touch code, the repo or the TV for day-to-day changes.
- **No servers, no running costs.** Everything is static files on GitHub Pages, rebuilt by GitHub Actions.
- **Readable from across the room, with the Hi5 look.** Dark, calm, bold type, Hi5 orange as the accent.
- **Survive an old TV browser.** The Flip runs Samsung Browser 2.2 on Tizen 5, which is Chromium 63 from 2017. Everything on the page has to work there.

## How it works

```
Google Calendar (public .ics)
        │  Apps Script sees the feed change (checked every 5 min) and starts the build;
        │  also daily, on push, manually, and GitHub's own schedule every 6 h as fallback
        ▼
GitHub Actions: build/build.py
  · expands recurring events, picks the next 21 within 90 days
  · finds an image and a link per event, resizes images, makes QR codes
  · decides location marker and organiser logos
  · writes data/events.json + data/img, data/qr, data/emoji
  · commits only if something on screen changed
        │
        ▼
GitHub Pages (static) ──► Flip browser: index.html + app.js + style.css
                           polls data/events.json every 5 min
```

Why a build step instead of reading the calendar in the browser: Google's .ics feed has no CORS headers, so the page cannot fetch it directly; and image lookup, resizing, QR generation and emoji handling are too heavy or impossible on the TV. The browser only reads one small JSON file from its own site.

## Calendar conventions (for whoever edits events)

| What | How |
|---|---|
| **Title** | Shown as written. Repeated spaces are collapsed. Emoji work (see below). |
| **At Hi5** | Leave location empty, or start it with "Gründerhuset" (any case; "Grunderhuset" also counts). Nothing extra is shown. |
| **Somewhere else** | Any other location gets a small orange pin and the place under the title in the list, and appears in the date line of the feature panel. A trailing ", Norway"/", Norge" is dropped. |
| **Online** | If the location contains webinar, online, digital, Teams, Zoom, Meet or "på nett", a screen icon replaces the pin. |
| **Link / QR code** | The first web link in the description (or URL field, or a location that is a link) becomes the QR code ("Mer info"). Dead links (404) are skipped. Drive and Google sign-in links never become QR codes. |
| **Image** | The first source that gives one, in this order: an image attached to the event (Drive sharing must be "anyone with the link"), then the links in the description, where a link can be an image itself or a page whose preview image (og:image) is used. A page that only has its site's default preview image is ignored. No image → a coloured card with the date. |
| **Organiser** | `Arrangør: Start i Vestfold` on its own line in the description (a leading `#` is fine; several names comma-separated). Without that line, a link to a partner's website decides. `Arrangør: Gründerhuset Hi5` shows nothing. Unknown names are shown as plain text. |

Changes reach the screen within about 15 minutes: up to 5 for the Apps Script to notice the feed changed, a minute for the build, and up to 5 for the screen to fetch the new data. Google's public feed itself can lag a few minutes behind an edit.

## What the screen shows

- **Left: feature panel.** One event at a time, rotating every 12 s: image (fills the box, cropping edges; this is intentional, also for posters), date line, title (up to 3 lines), QR code, and at the bottom "ARRANGØR" with partner logos.
- **Right: list.** 7 events per page, up to 2 pages. The header shows each page's date range ("24. nov–2. des · 9. des–6. jan"), current page underlined. The rotation runs through all events in order and the list flips page when the feature reaches the next page.
- **Touch.** Tapping a row features that event and restarts the 12 s from there. Tapping a date range jumps to that page.
- **Times.** "I dag"/"I morgen" for the next two days. Evenings that run past midnight read "19:00–00:00". Multi-day events get an explicit end date.
- **Status line** bottom right: "Oppdatert HH:MM", turns amber ("Ikke oppdatert siden …") if the data could not be fetched for an hour.

## Design choices and why

**Old browser (Chromium 63).** `app.js` is limited to ES2017: no `?.`, `??`, `Array.flat`, `Object.fromEntries`, `Promise.finally`. CI runs `es-check es2017` on every push. CSS avoids flex `gap`, `inset`, `aspect-ratio`, `clamp()`/`min()`/`max()` and `:is()`. `test.html` is a diagnostic page that reports screen size and feature support on the actual TV.

**Sizes in `vh`.** The TV browser applies its own zoom and reported a viewport of 1700×679 on a 1920×1080 panel. Sizing everything relative to viewport height keeps the layout identical whatever it decides.

**Type sizes.** First tuned large for reading across the room, then reduced on Hi5's request because titles were being cut off. Full titles won over maximum size. The list uses a date column beside the title (instead of a date line above it) to fit 7 rows; a crowded page shrinks its list text (to about 75 % at most) rather than cutting the last row.

**Dark theme.** The Flip's panel is glossy and reflects the ceiling lights; bold type and large images hold up better than thin text. Colours come from the Hi5 brand: charcoal `#333333`, orange `#EB651C`, amber `#FFB800`, font Poppins (self-hosted in `assets/fonts`).

**Emoji as images.** The TV's emoji font predates Unicode 12 and showed 🧇 as an empty box. The build replaces emoji in titles with Twemoji SVGs (`data/emoji/`).

**Partner logos in white.** Logos are shown white on the dark background via a CSS filter, so each file only needs a transparent background. Each partner has a `scale` in `partners.json` to even out how big logos look next to each other. Logos come from Hi5 (Innovasjon Norge) or the partners' own websites; white-only versions were recoloured dark so the files also work on light backgrounds.

**Partner detection by link.** startivestfold.no lists events from many organisers, so a Start i Vestfold link does not always mean they organise it. The `Arrangør:` line always overrides link detection.

**Self-updating page.** Each build stamps a hash of the page's own code (`app_version`) into `events.json`. When the screen sees a new value it reloads, but only after 11 minutes, because GitHub Pages caches files for 10 minutes and an earlier reload could pick up the old code and then never reload again. The page also reloads daily around 04:00 and keeps the last data in `localStorage`, so it shows something immediately after a restart even if the network is slow.

**Commits only on visible change.** The per-URL lookup cache (which sites were checked, which images found) lives in the Actions cache (`build/.cache/`), not in git. `events.json` is rewritten only when something other than its timestamp changed. This keeps history readable and avoids a commit every 15 minutes.

**Polite to linked sites.** Page and image lookups are cached for 24 h (failures for 1 h), and images already on disk are not downloaded again.

## Operations

**Automatic rebuild.** `trigger/apps-script.gs` runs in Google Apps Script. Every 5 minutes it fetches the public calendar feed, and when the content has changed it starts the build through GitHub's API; it also starts one build every morning. Setup steps are at the top of the file. It needs a fine-grained GitHub token stored as the script property `GITHUB_TOKEN`:

1. GitHub → your avatar → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
2. Resource owner: Grunderhuset-Hi5. Repository access: only `skjerm`. Repository permissions: Actions → Read and write.
3. Expiry: the longest allowed. Put a reminder in the calendar to renew it; when it expires, Google emails the script's owner that runs are failing, and the screen falls back to GitHub's 6-hourly schedule.

Why Apps Script and not GitHub's schedule: GitHub's cron is best-effort and on this repo ran 4 times in 24 h instead of 96. Apps Script time triggers run reliably, and building only when the feed changes uses few Actions minutes, which matters if the repo is private.

**Run the build now**: GitHub → Actions → "Build screen data" → "Run workflow", or

```sh
gh workflow run build.yml -R Grunderhuset-Hi5/skjerm --ref main
```

**Private repo.** The repo can be private with the site still public, but only on the GitHub Team plan (Pages from private repos is not in GitHub Free for organisations). Private repos also use the plan's Actions minutes (Team includes 3,000 per month); with change-triggered builds this setup uses a few hundred.

**Add a partner.** Put the logo in `assets/partners/` (SVG or PNG, transparent background, horizontal if available) and add an entry to `partners.json`:

```json
{
  "name": "Name as shown",
  "logo": "assets/partners/file.svg",
  "aliases": ["Other spellings people use"],
  "domains": ["partner.no"],
  "scale": 1.0
}
```

Aliases are matched ignoring case, accents, spaces and punctuation. Tests check that every logo file exists. Current partners: Start i Vestfold, Innovasjon Norge, Forskningsrådet, Kobben, Gründeriet, Colab, FRIENDS: (Friends at Work), Tønsberg og Færder Næringsforening.

**Change the page.** Edit `index.html`, `app.js` or `style.css` and push to `main`. The workflow runs tests and the ES2017 check, rebuilds data with the new `app_version`, and the TV reloads itself within about 20 minutes. For anything visual, try it on a branch first and screenshot at 1920×1080.

**Local development.**

```sh
pip install -r build/requirements.txt
python -m pytest -q          # build tests
python build/build.py        # fetch calendar, write data/
python -m http.server        # open http://localhost:8000/
npx es-check@8 es2017 app.js # what CI runs
```

## Repository layout

| Path | Purpose |
|---|---|
| `index.html`, `app.js`, `style.css` | The screen |
| `test.html`, `test.json` | Diagnostic page for the TV browser |
| `build/build.py` | Calendar → `data/` |
| `build/tests/` | pytest suite |
| `partners.json`, `assets/partners/` | Organiser partners and logos |
| `assets/logo-*.svg`, `assets/fonts/` | Hi5 logos (from Hi5's own files) and Poppins |
| `data/` | Generated by the build; do not edit by hand |
| `.github/workflows/build.yml` | Fallback schedule, checks, build, commit |
| `trigger/apps-script.gs` | Google Apps Script that starts the build when the calendar changes |

## Known issues and open questions

- **GitHub's schedule is unreliable.** It ran 4 times in the 24 h up to 2026-11-19 instead of every 15 minutes, which is why builds are started from Apps Script (see "Automatic rebuild"). The GitHub token that script uses must be renewed before it expires.
- **In a public repo, GitHub disables scheduled workflows after 60 days without activity.** This only affects the 6-hourly fallback, and not at all if the repo is private.
- **TFNF's logo is stacked (tower above three lines of text)** and is shown at double size to stay legible. A horizontal version would sit better in the row.
- **Not verified on the Flip over time:** whether it stays on overnight and survives power cycles depends on the TV's own power settings.

## Credits

Emoji graphics: [Twemoji](https://github.com/jdecked/twemoji), © Twitter, Inc. and other contributors, [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/). Pin and screen icons: Material Design icons, Apache 2.0. Font: Poppins, SIL Open Font License. Partner logos belong to their owners.
