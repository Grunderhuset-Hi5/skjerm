/*
 * Hi5 lobby screen. Renders data/events.json, which build/build.py writes.
 *
 * Target browser: Samsung Browser 2.2 on Tizen 5 = Chromium 63. Keep to
 * ES2017: no `?.`, `??`, Array.flat, Object.fromEntries, Promise.finally.
 * CI runs es-check to enforce this.
 */
(function () {
  'use strict';

  const DATA_URL = 'data/events.json';
  const STORAGE_KEY = 'hi5-skjerm-data';
  const POLL_MS = 5 * 60 * 1000;
  const ROTATE_MS = 12000;
  const FADE_MS = 500;
  const PAGE_SIZE = 7;  // rows per list page
  const MAX_PAGES = 2;
  const STALE_MS = 60 * 60 * 1000;
  const RELOAD_HOUR = 4; // daily fresh page load, early morning
  const RELOAD_DELAY_MS = 11 * 60 * 1000;

  const DAYS = ['søndag', 'mandag', 'tirsdag', 'onsdag', 'torsdag', 'fredag', 'lørdag'];
  const DAYS_SHORT = ['søn', 'man', 'tir', 'ons', 'tor', 'fre', 'lør'];
  const MONTHS = ['januar', 'februar', 'mars', 'april', 'mai', 'juni', 'juli',
    'august', 'september', 'oktober', 'november', 'desember'];
  const MONTHS_SHORT = ['jan', 'feb', 'mar', 'apr', 'mai', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'des'];
  // Material Design icons (Apache 2.0): map pin and screen, drawn in currentColor.
  const ICON_PIN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg>';
  const ICON_ONLINE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8v-2h5c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 14H3V5h18v12z"/></svg>';
  const CARD_STYLES = [
    { cls: 'card-orange', logo: 'assets/logo-hvit.svg' },
    { cls: 'card-charcoal', logo: 'assets/logo-hvit-orange.svg' },
    { cls: 'card-amber', logo: 'assets/logo-sort-orange.svg' }
  ];

  const state = {
    data: null,
    appVersion: null,
    currentId: null,
    currentMediaKey: null,
    reloadScheduled: false,
    lastFetchOk: null,
    loadedAt: Date.now(),
    rotateTimer: null,
    fadeTimer: null,
    listFadeTimer: null,
    page: null
  };

  const $ = (id) => document.getElementById(id);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------ dates

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function hhmm(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  function dayDiff(d, now) { return Math.round((startOfDay(d) - startOfDay(now)) / 86400000); }

  function parseEvent(raw) {
    return Object.assign({}, raw, { startDate: new Date(raw.start), endDate: new Date(raw.end) });
  }

  function isOngoing(ev, now) { return ev.startDate <= now && ev.endDate > now; }

  // "I dag", "I morgen", or "onsdag 2. desember" / "ons 2. des"
  function dayWord(d, now, short) {
    const diff = dayDiff(d, now);
    if (diff === 0) return 'I dag';
    if (diff === 1) return 'I morgen';
    return short
      ? DAYS_SHORT[d.getDay()] + ' ' + d.getDate() + '. ' + MONTHS_SHORT[d.getMonth()]
      : DAYS[d.getDay()] + ' ' + d.getDate() + '. ' + MONTHS[d.getMonth()];
  }

  function lastDay(ev) {
    // All-day end dates are exclusive (midnight after the last day).
    return new Date(ev.endDate.getTime() - 1);
  }

  function timeRange(ev) {
    if (ev.all_day) {
      const last = lastDay(ev);
      if (dayDiff(last, ev.startDate) === 0) return 'Hele dagen';
      return ev.startDate.getDate() + '.–' + last.getDate() + '. ' + MONTHS_SHORT[last.getMonth()];
    }
    if (ev.endDate <= ev.startDate) return hhmm(ev.startDate);
    if (dayDiff(ev.endDate, ev.startDate) === 0) return hhmm(ev.startDate) + '–' + hhmm(ev.endDate);
    return hhmm(ev.startDate) + ' til ' + dayWord(ev.endDate, ev.startDate, true) + ' ' + hhmm(ev.endDate);
  }

  // Shown only for events away from Hi5 (the build decides). Data cached from
  // before `place` existed falls back to the raw location.
  function placeOf(ev) { return ev.place === undefined ? ev.location : ev.place; }

  function featureWhen(ev, now) {
    const parts = isOngoing(ev, now)
      ? ['Pågår nå', ev.all_day ? timeRange(ev) : 'til ' + hhmm(ev.endDate)]
      : [dayWord(ev.startDate, now, false), timeRange(ev)];
    const place = placeOf(ev);
    if (place) parts.push(place);
    return parts.join(' · ');
  }

  // [day, time] for the list's date column
  function rowWhen(ev, now) {
    if (isOngoing(ev, now)) return ['Pågår nå', ev.all_day ? '' : 'til ' + hhmm(ev.endDate)];
    return [dayWord(ev.startDate, now, true), ev.all_day ? timeRange(ev) : hhmm(ev.startDate)];
  }

  // ------------------------------------------------------------ data

  function visibleEvents(now) {
    if (!state.data || !state.data.events) return [];
    return state.data.events
      .map(parseEvent)
      .filter((ev) => ev.endDate > now || (ev.endDate <= ev.startDate && ev.startDate >= now))
      .slice(0, PAGE_SIZE * MAX_PAGES);
  }

  function applyData(data, fromNetwork) {
    if (fromNetwork && data.app_version) {
      if (state.appVersion && state.appVersion !== data.app_version && !state.reloadScheduled) {
        // Page code changed. GitHub Pages caches files for 10 min, and reload()
        // may reuse cached app.js, so wait until those copies have expired.
        state.reloadScheduled = true;
        setTimeout(() => location.reload(), RELOAD_DELAY_MS);
      }
      if (!state.appVersion) state.appVersion = data.app_version;
    }
    state.data = data;
    preload(data.events || []);
    if (!state.rotateTimer) step();
  }

  function preload(events) {
    events.slice(0, PAGE_SIZE * MAX_PAGES).forEach((ev) => {
      if (ev.image) { new Image().src = ev.image; }
      if (ev.qr) { new Image().src = ev.qr; }
      (ev.title_parts || []).forEach((part) => { if (part.src) new Image().src = part.src; });
    });
  }

  function loadCached() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) applyData(JSON.parse(raw), false);
    } catch (e) { /* storage unavailable: fine, wait for network */ }
  }

  function fetchData() {
    return fetch(DATA_URL + '?t=' + Date.now(), { cache: 'no-store' })
      .then((res) => {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then((data) => {
        state.lastFetchOk = new Date();
        try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (e) { /* ignore */ }
        applyData(data, true);
        renderStatus();
      })
      .catch(() => { renderStatus(); });
  }

  // ------------------------------------------------------------ render

  function cardStyle(ev) {
    let h = 0;
    for (let i = 0; i < ev.title.length; i++) h = (h * 31 + ev.title.charCodeAt(i)) | 0;
    return CARD_STYLES[Math.abs(h) % CARD_STYLES.length];
  }

  function buildCard(ev, now) {
    const style = ev ? cardStyle(ev) : CARD_STYLES[1];
    const card = el('div', 'card ' + style.cls + (ev ? '' : ' card-empty'));
    if (ev) {
      const ongoing = isOngoing(ev, now);
      const diff = dayDiff(ev.startDate, now);
      const day = ongoing ? 'Pågår nå' : diff === 0 ? 'I dag' : diff === 1 ? 'I morgen' : DAYS[ev.startDate.getDay()];
      card.appendChild(el('div', 'card-day', day));
      card.appendChild(el('div', 'card-date', ev.startDate.getDate() + '. ' + MONTHS_SHORT[ev.startDate.getMonth()]));
      card.appendChild(el('div', 'card-time', timeRange(ev)));
    } else {
      card.appendChild(el('div', 'card-date', 'Ingen planlagte arrangementer'));
    }
    const logo = el('img', 'card-logo');
    logo.src = style.logo;
    logo.alt = '';
    card.appendChild(logo);
    return card;
  }

  function renderMedia(ev, now) {
    const media = $('media');
    media.innerHTML = '';
    if (ev && ev.image) {
      const photo = el('img', 'photo');
      photo.alt = '';
      photo.onerror = () => { media.innerHTML = ''; media.appendChild(buildCard(ev, now)); };
      photo.src = ev.image;
      media.appendChild(photo);
    } else {
      media.appendChild(buildCard(ev, now));
    }
  }

  // Titles with emoji arrive as parts; emoji are Twemoji SVGs because the TV's
  // emoji font is too old for newer ones like 🧇.
  function setTitle(node, ev) {
    node.textContent = '';
    if (!ev) return;
    if (!ev.title_parts) {
      node.textContent = ev.title;
      return;
    }
    ev.title_parts.forEach((part) => {
      if (part.src) {
        const img = el('img', 'emoji');
        img.src = part.src;
        img.alt = part.emoji;
        node.appendChild(img);
      } else {
        node.appendChild(document.createTextNode(part.text || ''));
      }
    });
  }

  // "Arrangør" with partner logos (white, via CSS) or names when there's no logo.
  function renderOrganisers(box, ev) {
    box.innerHTML = '';
    const orgs = (ev && ev.organisers) || [];
    if (!orgs.length) {
      box.className = 'org hidden';
      return;
    }
    box.className = 'org';
    box.appendChild(el('span', 'org-label', 'Arrangør'));
    // Logo height in vh: smaller when several partners share the row.
    const base = orgs.length <= 2 ? 4.2 : orgs.length === 3 ? 3.4 : 2.8;
    orgs.forEach((org) => {
      if (org.logo) {
        const img = el('img', 'org-logo');
        img.src = org.logo;
        img.alt = org.name;
        img.style.height = (base * (org.scale || 1)).toFixed(2) + 'vh';
        box.appendChild(img);
      } else {
        box.appendChild(el('span', 'org-name', org.name));
      }
    });
  }

  function renderFeature(ev, now) {
    // Rebuilding an unchanged image every rotation can flicker on the TV.
    // The key includes the date so a card's "I dag"/"I morgen" still updates.
    const mediaKey = ev ? ev.id + '|' + (ev.image || dayDiff(ev.startDate, now) + '|' + isOngoing(ev, now)) : 'empty';
    if (mediaKey !== state.currentMediaKey) {
      renderMedia(ev, now);
      state.currentMediaKey = mediaKey;
    }
    $('feature-when').textContent = ev ? featureWhen(ev, now) : '';
    setTitle($('feature-title'), ev);
    renderOrganisers($('feature-org'), ev);
    const qr = $('feature-qr');
    if (ev && ev.qr) {
      $('feature-qr-img').src = ev.qr;
      qr.className = 'qr';
    } else {
      qr.className = 'qr hidden';
    }
  }

  // ------------------------------------------------------------ pages
  // The list shows PAGE_SIZE events at a time. Rotation runs through all
  // events in order, so the list flips page when the feature reaches the next
  // page. The header shows each page's date range, current one highlighted.

  function pageOf(events, id) {
    for (let i = 0; i < events.length; i++) {
      if (events[i].id === id) return Math.floor(i / PAGE_SIZE);
    }
    return 0;
  }

  function shortDay(d) { return d.getDate() + '. ' + MONTHS_SHORT[d.getMonth()]; }

  // "9. des", "10.–22. des", "24. nov–9. des"
  function rangeLabel(evs) {
    const a = evs[0].startDate;
    const b = evs[evs.length - 1].startDate;
    if (dayDiff(b, a) === 0) return shortDay(a);
    if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
      return a.getDate() + '.–' + shortDay(b);
    }
    return shortDay(a) + '–' + shortDay(b);
  }

  function renderPages(events, page) {
    const box = $('pages');
    box.innerHTML = '';
    const count = Math.ceil(events.length / PAGE_SIZE);
    if (count <= 1) {
      box.appendChild(el('span', 'page-label', 'Kommende'));
      return;
    }
    for (let i = 0; i < count; i++) {
      if (i) box.appendChild(el('span', 'page-sep', '·'));
      const label = el('span', 'page' + (i === page ? ' active' : ''),
        rangeLabel(events.slice(i * PAGE_SIZE, (i + 1) * PAGE_SIZE)));
      label.setAttribute('data-page', String(i));
      box.appendChild(label);
    }
  }

  function renderList(events, activeId, now) {
    const page = pageOf(events, activeId);
    const pageChanged = state.page !== null && page !== state.page;
    state.page = page;
    renderPages(events, page);

    const list = $('list');
    clearTimeout(state.listFadeTimer);
    if (pageChanged) {
      list.className = 'list fading';
      state.listFadeTimer = setTimeout(() => {
        fillList(events, page, activeId, now);
        list.className = 'list';
      }, FADE_MS);
    } else {
      fillList(events, page, activeId, now);
      list.className = 'list';
    }
  }

  // A crowded page (long titles, several events away from Hi5) shrinks the
  // list text a little rather than cutting off the last row.
  function fitList() {
    const list = $('list');
    let scale = 1;
    list.style.setProperty('--row-scale', '1');
    while (scale > 0.76 && list.scrollHeight > list.clientHeight + 1) {
      scale -= 0.04;
      list.style.setProperty('--row-scale', scale.toFixed(2));
    }
  }

  function fillList(events, page, activeId, now) {
    const list = $('list');
    list.innerHTML = '';
    events.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).forEach((ev) => {
      const row = el('li', 'row' + (ev.id === activeId && events.length > 1 ? ' active' : ''));
      row.setAttribute('data-id', ev.id);
      const when = rowWhen(ev, now);
      const whenCol = el('div', 'row-when');
      whenCol.appendChild(el('div', 'row-day', when[0]));
      whenCol.appendChild(el('div', 'row-time', when[1]));
      row.appendChild(whenCol);
      const main = el('div', 'row-main');
      const title = el('div', 'row-title');
      setTitle(title, ev);
      main.appendChild(title);
      if (ev.place_kind && ev.place) {
        const place = el('div', 'row-place');
        const icon = el('span', 'place-icon');
        icon.innerHTML = ev.place_kind === 'online' ? ICON_ONLINE : ICON_PIN;
        place.appendChild(icon);
        place.appendChild(document.createTextNode(ev.place));
        main.appendChild(place);
      }
      row.appendChild(main);
      row.appendChild(el('div', 'progress'));
      list.appendChild(row);
    });
    fitList();
  }

  function renderStatus() {
    const status = $('status');
    if (!state.lastFetchOk) {
      status.textContent = state.data ? 'Venter på oppdatering' : 'Laster…';
      status.className = 'status';
      return;
    }
    const stale = Date.now() - state.lastFetchOk.getTime() > STALE_MS;
    status.textContent = (stale ? 'Ikke oppdatert siden ' : 'Oppdatert ') + hhmm(state.lastFetchOk);
    status.className = stale ? 'status stale' : 'status';
  }

  // ------------------------------------------------------------ rotation

  function nextEvent(events) {
    if (!events.length) return null;
    let idx = -1;
    for (let i = 0; i < events.length; i++) {
      if (events[i].id === state.currentId) { idx = i; break; }
    }
    return events[(idx + 1) % events.length];
  }

  // Show `ev` in the feature panel and restart the rotation timer from it.
  function display(ev, events, now) {
    const firstPaint = !state.rotateTimer;
    const changed = !ev || ev.id !== state.currentId;
    clearTimeout(state.rotateTimer);
    clearTimeout(state.fadeTimer);
    state.currentId = ev ? ev.id : null;

    // The list updates at once, so the highlight and its progress bar start
    // together with the timer below.
    renderList(events, state.currentId, now);

    const inner = $('feature');
    if (changed && !firstPaint) {
      inner.className = 'feature-inner fading';
      state.fadeTimer = setTimeout(() => {
        renderFeature(ev, now);
        inner.className = 'feature-inner';
      }, FADE_MS);
    } else {
      renderFeature(ev, now); // first paint, or same event: no fade
      inner.className = 'feature-inner';
    }
    renderStatus();
    state.rotateTimer = setTimeout(step, ROTATE_MS);
  }

  function step() {
    const now = new Date();
    const events = visibleEvents(now);
    display(nextEvent(events), events, now);
  }

  // Tapping a row on the touchscreen features that event; rotation continues from it.
  function onListTap(e) {
    const row = e.target.closest ? e.target.closest('.row') : null;
    if (!row) return;
    const now = new Date();
    const events = visibleEvents(now);
    const ev = events.find((x) => x.id === row.getAttribute('data-id'));
    if (ev) display(ev, events, now);
  }

  // Tapping a date range in the header jumps to that page's first event.
  function onPagesTap(e) {
    const label = e.target.closest ? e.target.closest('.page') : null;
    if (!label) return;
    const now = new Date();
    const events = visibleEvents(now);
    const ev = events[Number(label.getAttribute('data-page')) * PAGE_SIZE];
    if (ev) display(ev, events, now);
  }

  function maybeDailyReload() {
    const now = new Date();
    if (now.getHours() === RELOAD_HOUR && Date.now() - state.loadedAt > 2 * 60 * 60 * 1000) {
      location.reload();
    }
  }

  // ------------------------------------------------------------ start

  document.documentElement.style.setProperty('--rotate-ms', ROTATE_MS + 'ms');
  $('list').addEventListener('click', onListTap);
  $('pages').addEventListener('click', onPagesTap);
  window.addEventListener('resize', fitList);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitList);
  loadCached();
  fetchData();
  setInterval(fetchData, POLL_MS);
  setInterval(maybeDailyReload, 10 * 60 * 1000);
  if (!state.data) renderStatus();
}());
