/* ============================================================
   mock-backend.js  —  DEMO ONLY  •  remove when serving from FastAPI
   ------------------------------------------------------------
   This file exists so the static frontend can run in a browser
   with NO Python backend (e.g. design preview, GitHub Pages).

   It intercepts window.fetch for /api/* routes and answers them
   from localStorage, reproducing the CBT-I logic for the demo.

   In production, app.py (FastAPI) serves these same routes by
   importing the REAL, unit-tested functions from sleep_tracker.py.
   Delete the <script src="mock-backend.js"> tag in index.html and
   the page talks to the real backend unchanged.
   ============================================================ */
(function () {
  "use strict";

  var LS_ENTRIES = "sleep_demo_entries_v1";
  var LS_SETTINGS = "sleep_demo_settings_v1";
  var WINDOW_FLOOR = 300;          // 5h sleep-restriction floor
  var WINDOW_CEIL = 555;           // ~9h 15m ceiling

  /* ---------------- store ---------------- */
  function loadEntries() {
    try { return JSON.parse(localStorage.getItem(LS_ENTRIES)) || null; }
    catch (e) { return null; }
  }
  function saveEntries(list) {
    list.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
    localStorage.setItem(LS_ENTRIES, JSON.stringify(list));
  }
  function loadSettings() {
    try { return JSON.parse(localStorage.getItem(LS_SETTINGS)) || {}; }
    catch (e) { return {}; }
  }
  function saveSettings(s) { localStorage.setItem(LS_SETTINGS, JSON.stringify(s)); }

  /* ---------------- time helpers ---------------- */
  function timeToMin(t) {
    if (!t) return null;
    var p = t.split(":"); return (+p[0]) * 60 + (+p[1]);
  }
  function minutesToHM(min) {
    min = ((Math.round(min) % 1440) + 1440) % 1440;
    var h = Math.floor(min / 60), m = min % 60;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  }
  function durHM(min) {
    min = Math.max(0, Math.round(min));
    var h = Math.floor(min / 60), m = min % 60;
    return h + "h " + String(m).padStart(2, "0") + "m";
  }
  function timeInBed(bedtime, wake) {
    var b = timeToMin(bedtime), w = timeToMin(wake);
    if (b == null || w == null) return 0;
    var d = w - b; if (d <= 0) d += 1440;   // crosses midnight
    return d;
  }
  function efficiency(totalSleep, tib) {
    if (!tib) return 0;
    return Math.round((totalSleep / tib) * 1000) / 10;   // 1 decimal
  }

  /* ---------------- derive / decorate ---------------- */
  function decorate(e) {
    var tib = timeInBed(e.bedtime, e.wake_time);
    return Object.assign({}, e, {
      tib_min: tib,
      efficiency: efficiency(e.total_sleep_min, tib)
    });
  }
  function lastN(list, n) { return list.slice(Math.max(0, list.length - n)); }
  function avg(arr) { return arr.length ? arr.reduce(function (a, b) { return a + b; }, 0) / arr.length : 0; }

  /* ---------------- CBT-I core (demo port) ---------------- */
  function prescribedWindowMin(list) {
    var week = lastN(list, 7).map(decorate);
    if (!week.length) return 450;                 // default 7h30 before any data
    var avgSleep = avg(week.map(function (e) { return e.total_sleep_min; }));
    var w = Math.round(avgSleep / 15) * 15;        // round to 15 min
    return Math.min(WINDOW_CEIL, Math.max(WINDOW_FLOOR, w));
  }
  function weeklyAdjustment(list) {
    var week = lastN(list, 7).map(decorate);
    var current = prescribedWindowMin(list);
    if (week.length < 3) {
      return { direction: "maintain", minutes: 0, new_window_min: current,
        avg_efficiency: week.length ? Math.round(avg(week.map(function (e) { return e.efficiency; })) * 10) / 10 : null,
        reason: "Keep logging — at least a few nights are needed before adjusting your window." };
    }
    var avgEff = avg(week.map(function (e) { return e.efficiency; }));
    avgEff = Math.round(avgEff * 10) / 10;
    if (avgEff >= 90) {
      var ext = Math.min(WINDOW_CEIL, current + 15);
      return { direction: "extend", minutes: ext - current, new_window_min: ext, avg_efficiency: avgEff,
        reason: "7-night efficiency is " + avgEff + "% (\u2265 90%). You\u2019ve earned 15 more minutes in bed." };
    }
    if (avgEff < 85) {
      var red = Math.max(WINDOW_FLOOR, current - 15);
      return { direction: red < current ? "reduce" : "maintain", minutes: red - current, new_window_min: red, avg_efficiency: avgEff,
        reason: red < current
          ? "7-night efficiency is " + avgEff + "% (< 85%). Tighten the window by 15 minutes to consolidate sleep."
          : "Efficiency is " + avgEff + "%, but you\u2019re already at the 5-hour floor — hold steady." };
    }
    return { direction: "maintain", minutes: 0, new_window_min: current, avg_efficiency: avgEff,
      reason: "7-night efficiency is " + avgEff + "% (85\u201390%). Hold your current window for another week." };
  }
  function dailyNudges(list) {
    var n = [];
    if (!list.length) return ["Log tonight to start building your sleep picture."];
    var last = decorate(list[list.length - 1]);
    var week = lastN(list, 7).map(decorate);
    if (last.efficiency < 85)
      n.push("If you\u2019re awake for more than ~20 minutes, get out of bed and do something calm in dim light until sleepy.");
    if (last.restless_moments >= 6)
      n.push("Restlessness was high last night — try a 20-minute wind-down with no screens before your target bedtime.");
    // bedtime consistency
    var beds = week.map(function (e) { return timeToMin(e.bedtime); });
    if (beds.length >= 3) {
      var spread = Math.max.apply(null, beds) - Math.min.apply(null, beds);
      if (spread > 60) n.push("Your bedtime varied by over an hour this week. Anchoring it within \u00b130 minutes helps your body clock.");
    }
    if (last.total_sleep_min < 360)
      n.push("Short night. Avoid napping today and hold your fixed wake time to protect tonight\u2019s sleep drive.");
    n.push("Keep caffeine before early afternoon and dim the lights an hour before your target bedtime.");
    return n.slice(0, 4);
  }
  function positiveReinforcement(list) {
    var p = [];
    if (!list.length) return p;
    var week = lastN(list, 7).map(decorate);
    var avgEff = avg(week.map(function (e) { return e.efficiency; }));
    if (avgEff >= 85) p.push("Your sleep efficiency is averaging " + Math.round(avgEff) + "% this week — that\u2019s a strong, consolidated pattern.");
    // streak
    p.push("You\u2019ve logged " + loggingStreak(list) + " nights in a row. Consistency is the engine of CBT-I.");
    // improvement vs prior week
    var prior = list.slice(Math.max(0, list.length - 14), Math.max(0, list.length - 7)).map(decorate);
    if (prior.length >= 3) {
      var diff = avgEff - avg(prior.map(function (e) { return e.efficiency; }));
      if (diff >= 2) p.push("Efficiency is up about " + Math.round(diff) + " points versus last week. Keep doing what you\u2019re doing.");
    }
    return p;
  }
  function doctorFlags(list) {
    var f = [];
    if (!list.length) return f;
    var recent = lastN(list, 7).map(decorate);
    var lowEff = recent.filter(function (e) { return e.efficiency < 75; });
    if (lowEff.length >= 4)
      f.push("Sleep efficiency has stayed below 75% on " + lowEff.length + " of the last " + recent.length + " nights despite restriction.");
    var hrs = recent.filter(function (e) { return e.resting_hr != null; }).map(function (e) { return e.resting_hr; });
    if (hrs.some(function (h) { return h >= 100; })) f.push("Resting heart rate reached 100+ bpm overnight on one or more nights.");
    if (hrs.length && avg(hrs) < 45) f.push("Average overnight resting heart rate is unusually low (< 45 bpm).");
    var bigWake = recent.filter(function (e) { return (e.awakenings || 0) >= 5; });
    if (bigWake.length >= 3) f.push("Frequent awakenings (5+) on multiple nights may point to a treatable cause.");
    return f;
  }
  function adherenceNote(list) {
    var week = lastN(list, 7).map(decorate);
    if (week.length < 2) return "Not enough nights yet to assess how closely you\u2019re following the prescribed window.";
    var win = prescribedWindowMin(list);
    var avgTib = avg(week.map(function (e) { return e.tib_min; }));
    var diff = avgTib - win;
    if (Math.abs(diff) <= 20) return "You\u2019re tracking your prescribed window closely (within ~" + Math.round(Math.abs(diff)) + " min on average). Nicely done.";
    if (diff > 20) return "You\u2019re spending about " + Math.round(diff) + " min/night more in bed than prescribed. Extra time in bed dilutes sleep drive — try to hold the window.";
    return "You\u2019re in bed about " + Math.round(-diff) + " min/night less than prescribed. That\u2019s fine if you feel rested, but don\u2019t cut below the window on purpose.";
  }
  function restlessTrend(list) {
    var s = lastN(list, 6).map(decorate);
    if (s.length < 4) return { direction: "flat", message: "Not enough data for a restlessness trend yet." };
    var half = Math.floor(s.length / 2);
    var older = avg(s.slice(0, half).map(function (e) { return e.restless_moments; }));
    var newer = avg(s.slice(half).map(function (e) { return e.restless_moments; }));
    var d = newer - older;
    if (d <= -1) return { direction: "improving", message: "Restlessness is trending down — your nights are getting calmer." };
    if (d >= 1) return { direction: "worsening", message: "Restlessness is trending up over recent nights." };
    return { direction: "flat", message: "Restlessness has been steady recently." };
  }
  function loggingStreak(list) {
    if (!list.length) return 0;
    var dates = list.map(function (e) { return e.date; }).sort();
    var streak = 1;
    for (var i = dates.length - 1; i > 0; i--) {
      var cur = new Date(dates[i] + "T00:00:00");
      var prev = new Date(dates[i - 1] + "T00:00:00");
      var gap = Math.round((cur - prev) / 86400000);
      if (gap === 1) streak++; else break;
    }
    return streak;
  }

  function buildReport() {
    var list = loadEntries() || [];
    var settings = loadSettings();
    var wake = settings.wake || "06:30";
    var dec = list.map(decorate);
    var last = dec.length ? dec[dec.length - 1] : null;
    var week = lastN(dec, 7);

    var averages_7 = week.length ? {
      nights: week.length,
      avg_total_sleep_min: Math.round(avg(week.map(function (e) { return e.total_sleep_min; }))),
      avg_tib_min: Math.round(avg(week.map(function (e) { return e.tib_min; }))),
      avg_efficiency: Math.round(avg(week.map(function (e) { return e.efficiency; })) * 10) / 10,
      avg_restless: Math.round(avg(week.map(function (e) { return e.restless_moments; })) * 10) / 10,
      avg_awakenings: Math.round(avg(week.map(function (e) { return e.awakenings || 0; })) * 10) / 10,
      avg_resting_hr: (function () {
        var h = week.filter(function (e) { return e.resting_hr != null; }).map(function (e) { return e.resting_hr; });
        return h.length ? Math.round(avg(h)) : null;
      })()
    } : null;

    var window_min = prescribedWindowMin(list);
    var series = lastN(dec, 14).map(function (e) {
      return { date: e.date, efficiency: e.efficiency, restless: e.restless_moments,
        total_sleep_min: e.total_sleep_min, tib_min: e.tib_min };
    });

    return {
      generated_for: wake,
      wake_time: wake,
      last_night: last,
      averages_7: averages_7,
      prescribed_window_min: window_min,
      prescribed_window_hm: durHM(window_min),
      prescribed_bedtime: minutesToHM(timeToMin(wake) - window_min),
      weekly_adjustment: weeklyAdjustment(list),
      daily_nudges: dailyNudges(list),
      progress: positiveReinforcement(list),
      adherence_note: adherenceNote(list),
      doctor_flags: doctorFlags(list),
      restless_trend: restlessTrend(list),
      series_14: series
    };
  }

  function buildStats() {
    var list = (loadEntries() || []).map(decorate);
    if (!list.length) return { total_nights: 0, streak: 0, avg_efficiency_all: null, best_efficiency: null, first_date: null, last_date: null };
    var effs = list.map(function (e) { return e.efficiency; });
    return {
      total_nights: list.length,
      streak: loggingStreak(list),
      avg_efficiency_all: Math.round(avg(effs) * 10) / 10,
      best_efficiency: Math.max.apply(null, effs),
      avg_total_sleep_min: Math.round(avg(list.map(function (e) { return e.total_sleep_min; }))),
      first_date: list[0].date,
      last_date: list[list.length - 1].date
    };
  }

  /* ---------------- seed demo data (14 nights) ---------------- */
  function seedIfEmpty() {
    if (loadEntries()) return;
    var wake = "06:30";
    saveSettings({ wake: wake });
    var seed = [];
    var today = new Date();
    // hand-shaped arc: rough start -> improving as window tightens
    var profile = [
      { sleep: 372, tib: 510, restless: 8, wakes: 4, hr: 64 },
      { sleep: 348, tib: 498, restless: 9, wakes: 5, hr: 66 },
      { sleep: 401, tib: 505, restless: 7, wakes: 4, hr: 63 },
      { sleep: 366, tib: 492, restless: 7, wakes: 3, hr: 65 },
      { sleep: 410, tib: 486, restless: 6, wakes: 3, hr: 62 },
      { sleep: 388, tib: 470, restless: 6, wakes: 4, hr: 61 },
      { sleep: 421, tib: 468, restless: 5, wakes: 2, hr: 60 },
      { sleep: 405, tib: 455, restless: 5, wakes: 3, hr: 61 },
      { sleep: 432, tib: 462, restless: 4, wakes: 2, hr: 59 },
      { sleep: 418, tib: 448, restless: 4, wakes: 2, hr: 60 },
      { sleep: 441, tib: 470, restless: 3, wakes: 1, hr: 58 },
      { sleep: 426, tib: 452, restless: 4, wakes: 2, hr: 59 },
      { sleep: 449, tib: 471, restless: 3, wakes: 1, hr: 57 },
      { sleep: 438, tib: 460, restless: 3, wakes: 2, hr: 58 }
    ];
    for (var i = 0; i < profile.length; i++) {
      var d = new Date(today);
      d.setDate(today.getDate() - (profile.length - 1 - i));
      var date = d.toISOString().slice(0, 10);
      var p = profile[i];
      var wakeMin = timeToMin(wake);
      var bedMin = ((wakeMin - p.tib) % 1440 + 1440) % 1440;
      seed.push({
        date: date,
        bedtime: minutesToHM(bedMin),
        wake_time: wake,
        total_sleep_min: p.sleep,
        restless_moments: p.restless,
        awakenings: p.wakes,
        resting_hr: p.hr,
        notes: i === profile.length - 1 ? "Felt clearer this morning." : ""
      });
    }
    saveEntries(seed);
  }

  /* ---------------- fetch shim ---------------- */
  function json(body, status) {
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: status || 200, headers: { "Content-Type": "application/json" }
    }));
  }

  var realFetch = window.fetch ? window.fetch.bind(window) : null;

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
    var path = url.replace(/^https?:\/\/[^/]+/, "").split("?")[0];

    if (path.indexOf("/api/") !== 0) {
      return realFetch ? realFetch(input, init) : json({ error: "no network" }, 502);
    }

    var body = null;
    try { body = init && init.body ? JSON.parse(init.body) : null; } catch (e) { body = null; }

    // /api/entries
    if (path === "/api/entries" && method === "GET")
      return json((loadEntries() || []).map(decorate));

    if (path === "/api/entries" && method === "POST") {
      var list = loadEntries() || [];
      var entry = {
        date: body.date,
        bedtime: body.bedtime,
        wake_time: body.wake_time,
        total_sleep_min: +body.total_sleep_min,
        restless_moments: +body.restless_moments || 0,
        awakenings: body.awakenings === "" || body.awakenings == null ? null : +body.awakenings,
        resting_hr: body.resting_hr === "" || body.resting_hr == null ? null : +body.resting_hr,
        notes: body.notes || ""
      };
      var idx = list.findIndex(function (e) { return e.date === entry.date; });
      if (idx >= 0) list[idx] = entry; else list.push(entry);
      saveEntries(list);
      return json(decorate(entry), 200);
    }

    var delMatch = path.match(/^\/api\/entries\/(.+)$/);
    if (delMatch && method === "DELETE") {
      var date = decodeURIComponent(delMatch[1]);
      var l = (loadEntries() || []).filter(function (e) { return e.date !== date; });
      saveEntries(l);
      return json({ deleted: date }, 200);
    }

    if (path === "/api/report" && method === "GET") return json(buildReport());
    if (path === "/api/stats" && method === "GET") return json(buildStats());

    if (path === "/api/settings/wake" && method === "GET")
      return json({ wake: (loadSettings().wake) || "06:30" });
    if (path === "/api/settings/wake" && method === "PUT") {
      var s = loadSettings(); s.wake = body.wake; saveSettings(s);
      return json({ wake: s.wake }, 200);
    }

    return json({ error: "Not found", path: path, method: method }, 404);
  };

  // expose a reset for the demo
  window.__resetDemoData = function () {
    localStorage.removeItem(LS_ENTRIES);
    localStorage.removeItem(LS_SETTINGS);
    seedIfEmpty();
  };

  seedIfEmpty();
})();
