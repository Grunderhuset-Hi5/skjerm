# skjerm

Info screen for the lobby at Gründerhuset Hi5. Live at <https://grunderhuset-hi5.github.io/skjerm/>; the Samsung Flip's browser points there.

It shows upcoming events from the public calendar *Felleskalender Gründerhuset Hi5*: the next five in a list, with a large panel rotating through them every 12 s.

## Adding images and links to events

Edit the event in Google Calendar. For each event the screen uses, in order:

1. An image URL in the description (any public `.jpg`, `.png` or `.webp` link)
2. An image attached to the event (Drive sharing must be *anyone with the link*)
3. The preview image of the first web page linked in the description or location
4. Otherwise a Hi5-coloured card with the date

The first web link also becomes the QR code. Changes show up within about 30 minutes.

## How it works

- `.github/workflows/build.yml` runs `build/build.py` every 15 minutes. It fetches the calendar, expands recurring events, finds images and links, and commits `data/events.json`, resized images (`data/img/`) and QR codes (`data/qr/`) when anything changed.
- `index.html`, `app.js`, `style.css` render that JSON. The TV runs Samsung Browser 2.2, which uses the Chromium 63 engine, so `app.js` is limited to ES2017 (CI checks this) and the CSS avoids newer features.
- The page refetches data every 5 minutes. It reloads itself when the page code changes, and once a day around 04:00.
- `test.html` is a diagnostic page: screen size, browser features, overscan frame.

## Local development

```sh
pip install -r build/requirements.txt
python -m pytest -q
python build/build.py
python -m http.server  # then open http://localhost:8000/
```

Scheduled workflows are disabled by GitHub after 60 days without repository activity. The data commits normally keep the repository active; if the screen stops updating, check the Actions tab.
