/**
 * Hi5 lobby screen: start the GitHub build when the calendar changes.
 *
 * Checks Google's public .ics feed every 5 minutes and starts the
 * "Build screen data" workflow when the feed's content has changed.
 * Also starts one build every morning, so events moving into the build's
 * 90-day window and refreshed link previews are picked up.
 *
 * Why poll the feed instead of a calendar trigger: the build reads this
 * same public feed, which can lag a little behind edits. Building when the
 * feed itself changes avoids building before the edit is visible. The feed
 * is public, so this script can run under any Google account.
 *
 * Setup (once), at https://script.google.com:
 *   1. New project. Replace the contents of Code.gs with this file.
 *   2. Project settings → Script properties → add property GITHUB_TOKEN:
 *      a fine-grained GitHub token for Grunderhuset-Hi5/skjerm with
 *      "Actions: Read and write" (see README, "Automatic rebuild").
 *   3. Select setup in the toolbar, Run, and approve the permissions.
 * Google emails the project owner if a run fails, e.g. when the token expires.
 */

const FEED_URL = 'https://calendar.google.com/calendar/ical/' +
  'c_c601aaf767740129b6f90e9269a43adb02bfae28b4bb269626227be8c0087548' +
  '%40group.calendar.google.com/public/basic.ics';
const DISPATCH_URL =
  'https://api.github.com/repos/Grunderhuset-Hi5/skjerm/actions/workflows/build.yml/dispatches';

function setup() {
  ScriptApp.getProjectTriggers().forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('checkFeed').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('dailyBuild').timeBased().everyDays(1).atHour(5).create();
  PropertiesService.getScriptProperties().deleteProperty('FEED_HASH');
  checkFeed(); // records the current feed and starts a first build
}

function checkFeed() {
  const res = UrlFetchApp.fetch(FEED_URL, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) {
    console.warn('Calendar feed answered ' + res.getResponseCode() + '; trying again in 5 min');
    return;
  }
  // Google returns the events in a different order on every fetch and stamps
  // each with the fetch time (DTSTAMP), so compare the sorted events without it.
  const events = res.getContentText().split('BEGIN:VEVENT').slice(1)
    .map((block) => block.split('END:VEVENT')[0].replace(/^DTSTAMP:.*$/gm, '').trim())
    .sort();
  if (!events.length) {
    console.warn('Calendar feed had no events; ignoring this fetch');
    return;
  }
  const hash = Utilities.base64Encode(Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256, events.join('\n'), Utilities.Charset.UTF_8));
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('FEED_HASH') === hash) return;
  startBuild_('calendar changed');
  props.setProperty('FEED_HASH', hash); // only after the build was started
}

function dailyBuild() {
  startBuild_('daily');
}

function startBuild_(reason) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) throw new Error('Add the script property GITHUB_TOKEN first');
  const res = UrlFetchApp.fetch(DISPATCH_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() !== 204) {
    throw new Error('GitHub answered ' + res.getResponseCode() + ': ' + res.getContentText());
  }
  console.log('Build started (' + reason + ')');
}
