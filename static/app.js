/* ============================================================
   app.js  —  Sleep Tracker frontend
   Talks to the FastAPI backend over /api/* (same routes the
   demo shim answers). No CBT-I logic lives here — the server
   computes everything from sleep_tracker.py and we render it.
   ============================================================ */
(function () {
  "use strict";

  /* ---------------- api ---------------- */
  var api = {
    entries: function () { return get("/api/entries"); },
    report: function () { return get("/api/report"); },
    stats: function () { return get("/api/stats"); },
    getWake: function () { return get("/api/settings/wake"); },
    setWake: function (w) { return send("PUT", "/api/settings/wake", { wake: w }); },
    addEntry: function (e) { return send("POST", "/api/entries", e); },
    delEntry: function (d) { return send("DELETE", "/api/entries/" + encodeURIComponent(d)); },
    sync: function () { return send("POST", "/api/sync"); },
    settings: function () { return get("/api/settings"); },
    setSettings: function (s) { return send("PUT", "/api/settings", s); },
    setKey: function (k) { return send("PUT", "/api/settings/anthropic-key", { key: k }); },
    diet: function (d) { return get("/api/diet" + (d ? "?date=" + d : "")); },
    meals: function (d) { return get("/api/meals" + (d ? "?date=" + d : "")); },
    delMeal: function (id) { return send("DELETE", "/api/meals/" + id); },
    editMeal: function (id, f) { return send("PATCH", "/api/meals/" + id, f); },
    logWeight: function (w) { return send("POST", "/api/weight", w); },
    activities: function () { return get("/api/activities"); },
    insights: function () { return get("/api/insights"); },
    summary: function (d, refresh) { return get("/api/summary/daily?date=" + d + (refresh ? "&refresh=true" : "")); },
    uploadMeal: function (formData) { return fetch("/api/meals", { method: "POST", body: formData }).then(checkOk); }
  };
  function get(url) { return fetch(url).then(checkOk); }
  function send(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    }).then(checkOk);
  }
  function checkOk(r) {
    if (!r.ok) return r.text().then(function (t) { throw new Error(t || r.status); });
    return r.json();
  }

  /* ---------------- helpers ---------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") n.className = attrs[k];
      else if (k === "html") n.innerHTML = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else n.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(function (c) { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  function durHM(min) {
    if (min == null) return "—";
    min = Math.max(0, Math.round(min));
    return Math.floor(min / 60) + "h " + String(min % 60).padStart(2, "0") + "m";
  }
  function to12h(t) {
    if (!t) return { time: "—", ampm: "" };
    var p = t.split(":"), h = +p[0], m = p[1];
    var ap = h >= 12 ? "PM" : "AM";
    var hh = h % 12; if (hh === 0) hh = 12;
    return { time: hh + ":" + m, ampm: ap };
  }
  function fmtDate(d, opts) {
    return new Date(d + "T00:00:00").toLocaleDateString(undefined, opts || { weekday: "short", month: "short", day: "numeric" });
  }
  function effClass(e) { return e >= 85 ? "eff-good" : e >= 75 ? "eff-mid" : "eff-low"; }

  /* ---------------- charts ---------------- */
  var effChart = null, restlessChart = null;
  var COLORS = {
    eff: getCss("--c-eff") || "#4f6bff",
    effFill: getCss("--c-eff-fill") || "rgba(79,107,255,0.10)",
    restless: getCss("--c-restless") || "#9aa3c8",
    target: getCss("--c-target") || "#1f8a5b",
    grid: "rgba(15,21,48,0.06)",
    tick: "#7c8298"
  };
  function getCss(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

  function baseScales(yLabel, yMax) {
    return {
      x: { grid: { display: false }, ticks: { color: COLORS.tick, font: { size: 11 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } },
      y: {
        beginAtZero: true, max: yMax,
        grid: { color: COLORS.grid, drawBorder: false },
        border: { display: false },
        ticks: { color: COLORS.tick, font: { size: 11 }, padding: 6, callback: function (v) { return v + (yLabel || ""); } }
      }
    };
  }
  function drawCharts(series) {
    var labels = series.map(function (s) { return fmtDate(s.date, { month: "short", day: "numeric" }); });
    Chart.defaults.font.family = "Public Sans, sans-serif";

    var effCtx = $("#chart-eff");
    if (effChart) effChart.destroy();
    effChart = new Chart(effCtx, {
      type: "line",
      data: { labels: labels, datasets: [
        { label: "Sleep efficiency", data: series.map(function (s) { return s.efficiency; }),
          borderColor: COLORS.eff, backgroundColor: COLORS.effFill, borderWidth: 2.5,
          fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5, pointBackgroundColor: COLORS.eff },
        { label: "Target 85%", data: series.map(function () { return 85; }),
          borderColor: COLORS.target, borderWidth: 1.5, borderDash: [5, 5], pointRadius: 0, fill: false, tension: 0 }
      ] },
      options: chartOpts(baseScales("%", 100), function (c) { return c.dataset.label + ": " + (c.datasetIndex === 0 ? c.parsed.y + "%" : "85%"); })
    });

    var rCtx = $("#chart-restless");
    if (restlessChart) restlessChart.destroy();
    restlessChart = new Chart(rCtx, {
      type: "bar",
      data: { labels: labels, datasets: [
        { label: "Restless moments", data: series.map(function (s) { return s.restless; }),
          backgroundColor: COLORS.restless, borderRadius: 4, maxBarThickness: 18 }
      ] },
      options: chartOpts(baseScales("", undefined), function (c) { return c.parsed.y + " restless moments"; })
    });
  }
  function chartOpts(scales, label) {
    return {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0f1530", padding: 10, cornerRadius: 8, titleFont: { size: 12 }, bodyFont: { size: 12 },
          callbacks: { label: label }
        }
      },
      scales: scales
    };
  }

  /* ---------------- render: hero ---------------- */
  function renderHero(r) {
    var bt = to12h(r.prescribed_bedtime);
    var wk = to12h(r.wake_time);
    var adj = r.weekly_adjustment || {};
    var arrow = adj.direction === "extend" ? "↑" : adj.direction === "reduce" ? "↓" : "→";
    var verb = adj.direction === "extend" ? "Extend" : adj.direction === "reduce" ? "Reduce" : "Maintain";
    var mins = adj.minutes ? (adj.minutes > 0 ? "+" : "") + adj.minutes + " min" : "no change";

    $("#hero").innerHTML = "";
    $("#hero").appendChild(
      el("div", { class: "hero-grid" }, [
        el("div", {}, [
          el("div", { class: "kicker hero-kicker", text: "Tonight · target bedtime" }),
          el("div", { class: "hero-bedtime", html: bt.time + '<span class="ampm">' + bt.ampm + "</span>" }),
          el("div", { class: "hero-caption", text:
            "Be in bed at this time and out of bed at your fixed wake time. Don\u2019t go to bed earlier, even if you feel tired." }),
          el("div", { class: "hero-window", html:
            '<span class="dot"></span> Prescribed window <b>' + durHM(r.prescribed_window_min) + "</b>" })
        ]),
        el("div", { class: "hero-side" }, [
          el("div", { class: "hero-row" }, [ el("span", { class: "lbl", text: "Get in bed" }), el("span", { class: "val", text: bt.time + " " + bt.ampm }) ]),
          el("div", { class: "hero-row" }, [ el("span", { class: "lbl", text: "Fixed wake" }), el("span", { class: "val", text: wk.time + " " + wk.ampm }) ]),
          el("hr", { class: "subtle-rule", style: "border-color:var(--night-line);margin:4px 0" }),
          el("div", { class: "kicker hero-kicker", text: "This week\u2019s adjustment" }),
          el("div", { class: "adjust " + (adj.direction || "maintain") }, [
            el("span", { class: "arrow", text: arrow }), document.createTextNode(verb + " · " + mins)
          ]),
          el("div", { class: "adjust-reason", text: adj.reason || "" })
        ])
      ])
    );
  }

  /* ---------------- render: stats ---------------- */
  function statTile(k, v, unit, sub, badge) {
    return el("div", { class: "stat" }, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v", html: v + (unit ? '<span class="u">' + unit + "</span>" : "") }),
      badge ? el("span", { class: "eff-badge " + badge.cls, text: badge.text }) : (sub ? el("div", { class: "sub", text: sub }) : null)
    ]);
  }
  function renderStats(r) {
    var row = $("#stat-row"); row.innerHTML = "";
    var ln = r.last_night;
    if (ln) {
      row.appendChild(statTile("Last night · efficiency", ln.efficiency, "%", null,
        { cls: effClass(ln.efficiency), text: ln.efficiency >= 85 ? "On target" : ln.efficiency >= 75 ? "Building" : "Low" }));
      row.appendChild(statTile("Last night · slept", durHM(ln.total_sleep_min), "", "In bed " + durHM(ln.tib_min)));
    } else {
      row.appendChild(statTile("Last night", "—", "", "No entry yet"));
      row.appendChild(statTile("Time in bed", "—", "", ""));
    }
    var a = r.averages_7;
    if (a) {
      row.appendChild(statTile("7-night avg · efficiency", a.avg_efficiency, "%", a.nights + " nights logged"));
      row.appendChild(statTile("7-night avg · slept", durHM(a.avg_total_sleep_min), "",
        "Restless " + a.avg_restless + "/night"));
    } else {
      row.appendChild(statTile("7-night avg · efficiency", "—", "", "Need more nights"));
      row.appendChild(statTile("7-night avg · slept", "—", "", ""));
    }
  }

  /* ---------------- render: nudges / progress / flags ---------------- */
  function renderNudges(r) {
    var ul = $("#nudge-list"); ul.innerHTML = "";
    (r.daily_nudges || []).forEach(function (t) {
      ul.appendChild(el("li", { class: "nudge" }, [ el("span", { class: "bullet" }), el("span", { text: t }) ]));
    });
    // restless trend line
    var rt = r.restless_trend;
    $("#restless-trend").textContent = rt ? rt.message : "";
  }
  function renderProgress(r) {
    var ul = $("#progress-list"); ul.innerHTML = "";
    var items = r.progress || [];
    if (!items.length) {
      ul.appendChild(el("li", { class: "progress-item" }, [ el("span", { class: "check", text: "•" }), el("span", { text: "Log a few nights to start seeing your wins here." }) ]));
    }
    items.forEach(function (t) {
      ul.appendChild(el("li", { class: "progress-item" }, [ el("span", { class: "check", text: "✓" }), el("span", { text: t }) ]));
    });
    $("#adherence").innerHTML = "<b>Adherence — </b>" + (r.adherence_note || "");
  }
  function renderFactors(r) {
    var ul = $("#factors-list"); if (!ul) return;
    ul.innerHTML = "";
    var items = (r.factors || []).filter(function (f) { return f.strength !== "negligible"; });
    if (!items.length) {
      ul.appendChild(el("li", { class: "factor empty-factor", text:
        "Not enough data yet. Once you've synced ~2 weeks with stress, HRV, steps, etc., patterns will show up here." }));
      return;
    }
    items.slice(0, 6).forEach(function (f) {
      var dir = f.r > 0 ? "up" : "down";
      var body = [ el("div", { class: "factor-msg", text: f.message }) ];
      if (f.action) body.push(el("div", { class: "factor-action", text: f.action }));
      ul.appendChild(el("li", { class: "factor" }, [
        el("span", { class: "factor-chip " + dir, text: (f.r > 0 ? "+" : "") + f.r.toFixed(2) }),
        el("div", { class: "factor-body" }, body)
      ]));
    });
  }

  function renderFlags(r) {
    var box = $("#flags-wrap");
    var flags = r.doctor_flags || [];
    if (!flags.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "";
    box.innerHTML = "";
    box.appendChild(el("div", { class: "callout-flags" }, [
      el("div", { class: "ttl" }, [ el("span", { class: "ico", text: "!" }), document.createTextNode("Worth raising with a clinician") ]),
      (function () {
        var u = el("ul");
        flags.forEach(function (f) { u.appendChild(el("li", { text: f })); });
        return u;
      })()
    ]));
  }

  /* ---------------- render: history ---------------- */
  function renderHistory(entries) {
    var body = $("#history-body"); body.innerHTML = "";
    var empty = $("#history-empty");
    if (!entries.length) { empty.style.display = ""; $("#history-table").style.display = "none"; return; }
    empty.style.display = "none"; $("#history-table").style.display = "";
    entries.slice().reverse().forEach(function (e) {
      var bt = to12h(e.bedtime), wk = to12h(e.wake_time);
      var hasSleep = e.efficiency != null;
      var effCell = hasSleep
        ? el("td", { class: "num" }, [ el("span", { class: "mini-eff", text: e.efficiency + "%" }) ])
        : el("td", { class: "num", text: "—" });
      var dash = function (v) { return v == null ? "—" : String(v); };
      var tr = el("tr", {}, [
        el("td", { class: "date", text: fmtDate(e.date) }),
        el("td", { text: e.bedtime ? bt.time + " " + bt.ampm : "—" }),
        el("td", { text: e.wake_time ? wk.time + " " + wk.ampm : "—" }),
        el("td", { class: "num", text: durHM(e.total_sleep_min) }),
        el("td", { class: "num", text: durHM(e.tib_min) }),
        effCell,
        el("td", { class: "num", text: dash(e.restless_moments) }),
        el("td", { class: "num", text: dash(e.awakenings) }),
        el("td", { class: "num", text: e.resting_hr == null ? "—" : e.resting_hr + " bpm" }),
        el("td", { class: "num", text: e.steps == null ? "—" : e.steps.toLocaleString() }),
        el("td", { class: "num", text: dash(e.stress_avg) }),
        el("td", { class: "num", text: e.hrv_overnight == null ? "—" : Math.round(e.hrv_overnight) + " ms" }),
        el("td", { class: "num", text: e.caffeine_mg == null ? "—" : e.caffeine_mg + " mg" }),
        el("td", { class: "num", text: dash(e.rested) }),
        el("td", { class: "num", text: dash(e.daytime_sleepiness) }),
        el("td", {}, [ (function () {
          var b = el("button", { class: "del-btn", title: "Delete this day", html: "&times;" });
          b.addEventListener("click", function () { onDelete(e.date); });
          return b;
        })() ])
      ]);
      if (hasSleep) {
        var span = tr.querySelector(".mini-eff");
        span.style.color = e.efficiency >= 85 ? getCss("--good") : e.efficiency >= 75 ? getCss("--warn") : getCss("--bad");
      }
      body.appendChild(tr);
    });
  }

  /* ---------------- actions ---------------- */
  function onDelete(date) {
    if (!confirm("Delete the entry for " + fmtDate(date, { month: "long", day: "numeric", year: "numeric" }) + "?")) return;
    api.delEntry(date).then(refresh).catch(showErr);
  }

  function onSubmit(ev) {
    ev.preventDefault();
    var f = ev.target;
    if (!f.date.value) {
      return flash("form-flash", "Pick a date.", "err");
    }
    // Build a sparse payload: only send fields the user actually filled, so the
    // server merge never wipes the Garmin sleep numbers.
    var entry = { date: f.date.value };
    var textFields = ["caffeine_last_time", "last_meal_time", "notes"];
    var numFields = ["caffeine_mg", "nap_min", "mood", "rested", "daytime_sleepiness"];
    numFields.forEach(function (k) { if (f[k] && f[k].value !== "") entry[k] = f[k].value; });
    textFields.forEach(function (k) { if (f[k] && f[k].value !== "") entry[k] = f[k].value; });
    entry.wind_down = f.wind_down.checked ? 1 : 0;
    entry.screens_before_bed = f.screens_before_bed.checked ? 1 : 0;
    api.addEntry(entry).then(function () {
      flash("form-flash", "Check-in saved for " + entry.date + " \u2014 it'll merge with that night's sleep.", "ok");
      ev.target.reset();
      applyCheckinMode("tonight");
      refresh();
    }).catch(showErr);
  }

  function onSaveWake(ev) {
    ev.preventDefault();
    var w = $("#wake-setting").value;
    api.setWake(w).then(function () {
      flash("wake-flash", "Fixed wake time saved.", "ok");
      refresh();
    }).catch(showErr);
  }

  function flash(id, msg, kind) {
    var n = document.getElementById(id);
    n.textContent = msg; n.className = "form-flash " + kind;
    clearTimeout(n._t); n._t = setTimeout(function () { n.textContent = ""; }, 3200);
  }
  function showErr(e) { flash("form-flash", "Something went wrong: " + (e.message || e), "err"); }

  /* ---------------- load + render everything ---------------- */
  function refresh() {
    return Promise.all([api.report(), api.entries(), api.stats()]).then(function (res) {
      var report = res[0], entries = res[1], stats = res[2];
      if (report.garmin_date_offset != null) {
        garminOffset = report.garmin_date_offset;
        if (checkinMode !== "custom") applyCheckinMode(checkinMode);
      }
      renderHero(report);
      renderStats(report);
      renderNudges(report);
      renderProgress(report);
      renderFactors(report);
      renderFlags(report);
      renderHistory(entries);
      drawCharts(report.series_14 || []);
      renderStreak(stats);
    }).catch(function (e) {
      console.error(e);
      var hero = $("#hero");
      if (hero) hero.innerHTML = '<div style="color:var(--night-mut);padding:8px">Could not reach the backend. ' +
        'Start it with <code>uvicorn app:app --reload</code>.</div>';
    });
  }
  function renderStreak(stats) {
    var n = $("#streak-pill");
    if (!n) return;
    if (stats && stats.total_nights) {
      n.textContent = stats.streak + "-night streak · " + stats.total_nights + " logged";
      n.style.display = "";
    } else { n.style.display = "none"; }
  }

  /* ---------------- init ---------------- */
  function todayISO() { return new Date().toLocaleDateString("en-CA"); }
  function isoPlus(days) {
    var d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + days);
    return d.toLocaleDateString("en-CA");
  }

  // Garmin files a night under the wake-up morning, so "tonight's" sleep is
  // stored under today + offset (normally +1). We learn the offset from syncs.
  var garminOffset = 1;
  var checkinMode = "tonight";

  function applyCheckinMode(mode) {
    checkinMode = mode;
    ["tonight", "lastnight", "custom"].forEach(function (m) {
      var b = $("#for-" + m); if (b) b.classList.toggle("seg-on", m === mode);
    });
    var hint = $("#attach-hint");
    if (mode === "custom") {
      hint.textContent = "Pick the sleep date this attaches to.";
      return;
    }
    var d = isoPlus(mode === "tonight" ? garminOffset : garminOffset - 1);
    $("#form-date").value = d;
    hint.textContent = (mode === "tonight"
      ? "→ attaches to the sleep you're about to get (Garmin syncs it next morning)."
      : "→ attaches to last night's sleep.") + " Sleep date: " + d;
  }

  /* ---------------- tabs ---------------- */
  var TABS = ["today", "sleep", "diet", "activity", "body", "insights"];
  function showTab(name) {
    if (TABS.indexOf(name) < 0) name = "today";
    if (name !== "diet" && typeof closeCam === "function") closeCam();  // release webcam
    TABS.forEach(function (t) {
      var sec = $("#tab-" + t); if (sec) sec.hidden = (t !== name);
    });
    document.querySelectorAll(".tabbtn").forEach(function (b) {
      b.classList.toggle("tab-on", b.getAttribute("data-tab") === name);
    });
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    loadTab(name);
  }
  function loadTab(name) {
    if (name === "today") loadToday();
    else if (name === "sleep") refresh();           // re-render so charts size correctly
    else if (name === "diet") loadDiet();
    else if (name === "activity") loadActivity();
    else if (name === "body") loadBody();
    else if (name === "insights") loadInsights();
  }

  /* ---------------- today (overview) ---------------- */
  function loadToday() {
    Promise.all([api.report(), api.diet(todayISO()), api.stats(), api.entries()]).then(function (res) {
      renderToday(res[0], res[1], res[2], res[3]);
    }).catch(showErr);
    api.summary(todayISO(), false).then(function (s) {
      $("#today-summary").innerHTML = s.summary
        ? s.summary.replace(/\n/g, "<br>")
        : "No summary yet — tap Refresh to generate one for today.";
    }).catch(function () { $("#today-summary").textContent = ""; });
  }
  function renderToday(report, diet, stats, entries) {
    var bt = to12h(report.prescribed_bedtime);
    var ln = report.last_night;
    var todayRow = null, today = todayISO();
    (entries || []).forEach(function (e) { if (e.date === today) todayRow = e; });
    $("#today-hero").innerHTML = "";
    $("#today-hero").appendChild(el("div", { class: "hero-grid" }, [
      el("div", {}, [
        el("div", { class: "kicker hero-kicker", text: "Tonight · target bedtime" }),
        el("div", { class: "hero-bedtime", html: bt.time + '<span class="ampm">' + bt.ampm + "</span>" }),
        el("div", { class: "hero-caption", text: "Your whole-health snapshot for today. Switch tabs for the detail." })
      ]),
      el("div", { class: "hero-side" }, [
        el("div", { class: "hero-row" }, [el("span", { class: "lbl", text: "Last night" }), el("span", { class: "val", text: ln ? ln.efficiency + "%" : "—" })]),
        el("div", { class: "hero-row" }, [el("span", { class: "lbl", text: "Net calories" }), el("span", { class: "val", text: diet.net_calories == null ? "—" : diet.net_calories })]),
        el("div", { class: "hero-row" }, [el("span", { class: "lbl", text: "Weight" }), el("span", { class: "val", text: diet.weight == null ? "—" : diet.weight + " " + diet.weight_unit })])
      ])
    ]));
    var row = $("#today-stats"); row.innerHTML = "";
    row.appendChild(statTile("Last night · sleep", ln ? durHM(ln.total_sleep_min) : "—", "", ln ? ln.efficiency + "% efficiency" : "no data"));
    row.appendChild(statTile("Calories in", diet.calories_in || 0, " kcal", diet.targets && diet.targets.calorie_target ? "target " + diet.targets.calorie_target : "set a goal"));
    row.appendChild(statTile("Steps today", todayRow && todayRow.steps != null ? todayRow.steps.toLocaleString() : "—", "",
      todayRow && todayRow.active_calories != null ? todayRow.active_calories + " active kcal" : ""));
    row.appendChild(statTile("Today · meals", (diet.meals || []).length, "", "logged"));
    var g = $("#today-glance"); g.innerHTML = "";
    function gi(txt) { g.appendChild(el("li", { class: "progress-item" }, [el("span", { class: "check", text: "•" }), el("span", { text: txt })])); }
    if (ln) gi("Slept " + durHM(ln.total_sleep_min) + " at " + ln.efficiency + "% efficiency; resting HR " + (ln.resting_hr || "—") + ".");
    gi("Ate " + (diet.calories_in || 0) + " kcal" + (diet.calories_out ? ", burned " + diet.calories_out + " (net " + diet.net_calories + ")." : " so far."));
    if (todayRow && todayRow.steps != null) gi(todayRow.steps.toLocaleString() + " steps so far today" + (todayRow.floors_climbed != null ? ", " + todayRow.floors_climbed + " floors." : "."));
    if ((report.daily_nudges || []).length) gi(report.daily_nudges[0]);
    if (!ln && !diet.calories_in) gi("Tap Sync to pull today's Garmin data, or log a meal under Diet.");
    renderMealGallery(diet.meals || [], "#today-meals", false);
  }

  /* ---------------- sync ---------------- */
  function onSync() {
    var btn = $("#sync-btn"); btn.disabled = true;
    $("#sync-status").textContent = "Syncing…";
    api.sync().then(function (r) {
      $("#sync-status").textContent = "Synced " + r.saved + " nights, " + r.activities + " activities";
      btn.disabled = false;
      refresh();
      loadTab(currentTab());
    }).catch(function (e) {
      $("#sync-status").textContent = "Sync failed";
      btn.disabled = false; showErr(e);
    });
  }
  function currentTab() { return (location.hash || "#today").slice(1); }
  function showSyncTime(t) { if (t) $("#sync-status").textContent = "Last synced " + fmtDate(t.slice(0, 10)); }

  /* ---------------- diet (macro tracker) ---------------- */
  var dietCharts = {};
  var dietDate = null;
  function addDaysISO(iso, n) { var d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toLocaleDateString("en-CA"); }
  function loadDiet() {
    if (!dietDate) dietDate = todayISO();
    $("#diet-date-sel").value = dietDate;
    $("#meal-date").value = dietDate;
    if (!$("#w-date").value) $("#w-date").value = todayISO();
    $("#diet-day-title").textContent = dietDate === todayISO() ? "Today"
      : fmtDate(dietDate, { weekday: "long", month: "short", day: "numeric" });
    $("#diet-next").disabled = dietDate >= todayISO();
    Promise.all([api.diet(dietDate), api.meals()]).then(function (res) {
      renderDiet(res[0], res[1]);
    }).catch(showErr);
    api.settings().then(fillSettings);
  }
  function _mealsByDay(allMeals) {
    var byDate = {};
    allMeals.forEach(function (m) {
      var b = byDate[m.date] = byDate[m.date] || { cal: 0, p: 0, c: 0, f: 0 };
      b.cal += m.calories || 0; b.p += m.protein_g || 0;
      b.c += m.carbs_g || 0; b.f += m.fat_g || 0;
    });
    return byDate;
  }
  function _last7Avg(byDate, key) {
    var days = [];
    for (var i = 6; i >= 0; i--) { var d = isoPlus(-i); if (byDate[d]) days.push(byDate[d][key]); }
    if (!days.length) return null;
    return Math.round(days.reduce(function (s, v) { return s + v; }, 0) / days.length);
  }
  function fillSettings(s) {
    $("#g-unit").value = s.weight_unit || "lb";
    var ul = $("#w-unit-label"); if (ul) ul.textContent = "(" + (s.weight_unit || "lb") + ")";
    $("#g-weight").value = s.weight_goal || "";
    if (s.weight_pace) $("#g-pace").value = s.weight_pace;
    $("#key-state").textContent = s.anthropic_key_set ? "✓ key saved" : "no key yet";
    renderTargets(s.targets);
    if (s.last_synced) showSyncTime(s.last_synced);
  }
  function renderTargets(t) {
    var box = $("#computed-targets"); if (!box) return;
    if (!t || t.calorie_target == null) {
      box.innerHTML = "<b>Targets — </b>" + ((t && t.basis) || "Set a goal and sync Garmin to compute targets.");
      return;
    }
    box.innerHTML = "<b>Computed targets:</b> " + t.calorie_target + " kcal/day · "
      + t.protein_g + "g protein · " + t.carbs_g + "g carbs · " + t.fat_g + "g fat. "
      + '<span class="note">' + t.basis + "</span>";
  }
  function ring(label, val, goal, unit) {
    var pct = goal ? Math.min(100, Math.round((val / goal) * 100)) : null;
    return el("div", { class: "stat" }, [
      el("div", { class: "k", text: label }),
      el("div", { class: "v", html: Math.round(val || 0) + (goal ? '<span class="u">/' + Math.round(goal) + unit + "</span>" : '<span class="u">' + unit + "</span>") }),
      el("div", { class: "sub", text: pct == null ? "set a goal" : pct + "% of goal" })
    ]);
  }
  function _cssvar(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim() || v; }
  function calorieDial(consumed, target) {
    consumed = Math.round(consumed || 0);
    var size = 220, stroke = 20, r = (size - stroke) / 2, circ = 2 * Math.PI * r;
    var pct = target ? consumed / target : 0;
    var remaining = target ? Math.round(target - consumed) : null;
    var over = remaining != null && remaining < 0;
    var col = !target ? _cssvar("--accent") : over ? _cssvar("--bad")
      : pct >= 0.9 ? _cssvar("--warn") : _cssvar("--good");
    var off = circ * (1 - Math.min(1, Math.max(0, pct)));
    var big = target == null ? consumed : Math.abs(remaining);
    var lab = target == null ? "kcal eaten" : over ? "kcal over" : "kcal left";
    var sub = target == null ? "set a goal" : (consumed + " / " + target + " kcal");
    var cx = size / 2;
    var svg = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">'
      + '<circle cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" stroke="' + _cssvar("--line") + '" stroke-width="' + stroke + '"/>'
      + '<circle cx="' + cx + '" cy="' + cx + '" r="' + r + '" fill="none" stroke="' + col + '" stroke-width="' + stroke
      + '" stroke-linecap="round" stroke-dasharray="' + circ.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1)
      + '" transform="rotate(-90 ' + cx + ' ' + cx + ')"/></svg>';
    return el("div", { class: "cal-dial", html: svg
      + '<div class="dial-center"><div class="dial-num" style="color:' + col + '">' + big + '</div>'
      + '<div class="dial-label">' + lab + '</div><div class="dial-sub">' + sub + '</div></div>' });
  }
  function macroBar(label, val, target, cls) {
    val = Math.round(val || 0);
    var pct = target ? Math.min(100, Math.round(val / target * 100)) : 0;
    var left = target == null ? null : Math.round(target - val);
    var right = target ? (val + " / " + Math.round(target) + " g") : (val + " g");
    var sub = target == null ? "set a goal" : (left >= 0 ? left + "g left" : Math.abs(left) + "g over");
    return el("div", { class: "macrobar " + cls }, [
      el("div", { class: "macrobar-top" }, [
        el("span", { class: "macrobar-label", text: label }),
        el("span", { class: "macrobar-val", text: right })]),
      el("div", { class: "macrobar-track" }, [el("div", { class: "macrobar-fill", style: "width:" + pct + "%" })]),
      el("div", { class: "macrobar-sub", text: sub })
    ]);
  }
  function renderDiet(d, allMeals) {
    var t = d.targets || {};
    // --- macro dashboard for the selected day ---
    $("#cal-dial").innerHTML = ""; $("#cal-dial").appendChild(calorieDial(d.calories_in, t.calorie_target));
    $("#bar-protein").innerHTML = ""; $("#bar-protein").appendChild(macroBar("Protein", d.protein_g, t.protein_g, "protein"));
    $("#bar-carbs").innerHTML = ""; $("#bar-carbs").appendChild(macroBar("Carbs", d.carbs_g, t.carbs_g, "carbs"));
    $("#bar-fat").innerHTML = ""; $("#bar-fat").appendChild(macroBar("Fat", d.fat_g, t.fat_g, "fat"));
    var en = $("#diet-energy-line");
    if (d.calories_out) {
      en.innerHTML = "Eaten <b>" + d.calories_in + "</b> · burned <b>" + d.calories_out
        + "</b> · net <b>" + d.net_calories + "</b> kcal";
    } else {
      en.innerHTML = "Eaten <b>" + d.calories_in + "</b> kcal · "
        + (t.calorie_target ? "target <b>" + t.calorie_target + "</b>" : "set a goal to see your target")
        + " · sync Garmin for calories burned";
    }
    // --- this day's meals (editable) ---
    renderDayMeals(d.meals || []);

    // --- weekly snapshot tiles ---
    var byDate = _mealsByDay(allMeals);
    var avgCal = _last7Avg(byDate, "cal"), avgP = _last7Avg(byDate, "p"),
        avgC = _last7Avg(byDate, "c"), avgF = _last7Avg(byDate, "f");
    var row = $("#diet-stats"); row.innerHTML = "";
    row.appendChild(ring("Avg calories · 7d", avgCal || 0, t.calorie_target, " kcal"));
    row.appendChild(ring("Avg protein · 7d", avgP || 0, t.protein_g, " g"));
    row.appendChild(ring("Avg carbs · 7d", avgC || 0, t.carbs_g, " g"));
    row.appendChild(ring("Avg fat · 7d", avgF || 0, t.fat_g, " g"));
    renderTargets(t);

    // --- history + charts ---
    var hist = allMeals.slice().sort(function (a, b) {
      return (b.date + (b.time || "")) < (a.date + (a.time || "")) ? -1 : 1;
    }).slice(0, 30);
    renderMealGallery(hist, "#meal-gallery", true);
    lineChart("chart-weight", dietCharts, (d.weight_series || []).map(function (w) { return { date: w.date, v: w.weight }; }), "Weight", COLORS.eff);
    barChart("chart-cal", dietCharts, (d.calorie_series || []).map(function (c) { return { date: c.date, v: c.calories }; }), "kcal");
  }
  function renderDayMeals(meals) {
    var g = $("#day-meals"); g.innerHTML = "";
    var tot = meals.reduce(function (s, m) { return s + (m.calories || 0); }, 0);
    $("#day-total-note").textContent = meals.length ? (Math.round(tot) + " kcal") : "";
    if (!meals.length) { g.appendChild(el("div", { class: "empty", text: "No meals logged this day." })); return; }
    meals.slice().sort(function (a, b) { return (a.time || "") < (b.time || "") ? -1 : 1; })
      .forEach(function (m) { g.appendChild(mealRow(m)); });
  }
  function mealRow(m) {
    var statusTxt = m.status === "pending" ? "analyzing…" : m.status === "failed" ? "analysis failed" : "";
    var macros = m.calories == null ? statusTxt
      : ("P " + (m.protein_g || 0) + " · C " + (m.carbs_g || 0) + " · F " + (m.fat_g || 0));
    var row = el("div", { class: "meal-row" });
    var view = el("div", { class: "meal-row-view" }, [
      m.photo_url ? el("img", { class: "meal-row-thumb", src: m.photo_url, alt: "" }) : null,
      el("div", { class: "meal-row-main" }, [
        el("div", { class: "meal-row-title", text: (m.time ? m.time + "  " : "") + (m.ai_description || m.notes || "Meal") }),
        el("div", { class: "meal-row-macros", text: macros })
      ]),
      el("div", { class: "meal-row-cal", html: (m.calories == null ? "—" : m.calories) + '<span class="u"> kcal</span>' }),
      (function () {
        var pencil = el("button", { class: "icon-btn", title: "Edit", html: "✎" });
        var del = el("button", { class: "icon-btn", title: "Delete", html: "&times;" });
        del.addEventListener("click", function () { api.delMeal(m.id).then(loadDiet); });
        pencil.addEventListener("click", function () { row.classList.toggle("editing"); });
        return el("div", { class: "meal-row-actions" }, [pencil, del]);
      })()
    ]);
    var edit = el("div", { class: "meal-row-edit" }, [
      _ni("cal", "kcal", m.calories), _ni("p", "protein", m.protein_g),
      _ni("c", "carbs", m.carbs_g), _ni("f", "fat", m.fat_g),
      (function () {
        var save = el("button", { class: "btn btn-primary", text: "Save" });
        save.addEventListener("click", function () {
          api.editMeal(m.id, {
            calories: row.querySelector(".mi-cal").value,
            protein_g: row.querySelector(".mi-p").value,
            carbs_g: row.querySelector(".mi-c").value,
            fat_g: row.querySelector(".mi-f").value
          }).then(loadDiet).catch(showErr);
        });
        return save;
      })()
    ]);
    row.appendChild(view); row.appendChild(edit);
    return row;
  }
  function _ni(key, label, val) {
    return el("label", { class: "meal-edit-field" }, [
      el("span", { text: label }),
      el("input", { type: "number", step: "1", class: "mi-" + key, value: (val == null ? "" : val) })
    ]);
  }
  function renderMealGallery(meals, sel, withDates) {
    var g = $(sel || "#meal-gallery"); if (!g) return;
    g.innerHTML = "";
    if (!meals.length) { g.appendChild(el("div", { class: "empty", text: withDates ? "No meals logged yet." : "No meals logged today." })); return; }
    meals.forEach(function (m) {
      var when = (withDates ? fmtDate(m.date) + (m.time ? " · " + m.time : "")
                            : (m.time || ""));
      var card = el("div", { class: "meal-card" }, [
        m.photo_url ? el("img", { class: "meal-thumb", src: m.photo_url, alt: "" }) : el("div", { class: "meal-thumb meal-noimg", text: "no photo" }),
        el("div", { class: "meal-body" }, [
          when ? el("div", { class: "meal-when", text: when }) : null,
          el("div", { class: "meal-cal", text: (m.calories == null ? "—" : m.calories + " kcal") + (m.status === "pending" ? " · analyzing…" : m.status === "failed" ? " · analysis failed" : "") }),
          el("div", { class: "meal-macros", text: m.calories == null ? (m.notes || "") : ("P " + (m.protein_g || 0) + " · C " + (m.carbs_g || 0) + " · F " + (m.fat_g || 0)) }),
          el("div", { class: "meal-desc", text: m.ai_description || m.notes || "" })
        ]),
        (function () { var b = el("button", { class: "del-btn", title: "Delete", html: "&times;" }); b.addEventListener("click", function () { api.delMeal(m.id).then(function () { loadTab(currentTab()); }); }); return b; })()
      ]);
      g.appendChild(card);
    });
  }
  function aiBusy(msg) { $("#ai-overlay-msg").textContent = msg || "Asking Claude…"; $("#ai-overlay").hidden = false; }
  function aiDone() { $("#ai-overlay").hidden = true; }

  function onMealSubmit(ev) {
    ev.preventDefault();
    var file = $("#meal-photo").files[0] || camBlob;
    var notes = ($("#meal-notes").value || "").trim();
    if (!file && !notes) {
      return flash("meal-flash", "Add a photo or describe the meal.", "err");
    }
    var fd = new FormData();
    if (file) fd.append("photo", file, file.name || "camera.jpg");
    fd.append("date", $("#meal-date").value || todayISO());
    if ($("#meal-time").value) fd.append("time", $("#meal-time").value);
    fd.append("notes", notes);
    aiBusy(file ? "Analyzing your photo…" : "Estimating from your description…");
    flash("meal-flash", "", "ok");
    api.uploadMeal(fd).then(function (m) {
      aiDone();
      if (m && m.status === "failed") {
        flash("meal-flash", "Analysis failed: " + (m.ai_description || "check your API key"), "err");
      } else {
        flash("meal-flash", m && m.calories != null ? ("Saved · " + m.calories + " kcal") : "Saved.", "ok");
      }
      ev.target.reset(); camBlob = null;
      $("#meal-preview").hidden = true; $("#meal-date").value = dietDate || todayISO();
      loadDiet();
    }).catch(function (e) { aiDone(); flash("meal-flash", "Failed: " + (e.message || e), "err"); });
  }

  /* ---------------- camera capture ---------------- */
  var camStream = null, camBlob = null;
  function openCam() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      // no webcam API — fall back to the file input (which opens the camera on phones)
      $("#meal-photo").click();
      return;
    }
    navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1920 } }, audio: false
    }).then(function (stream) {
      camStream = stream;
      $("#cam-video").srcObject = stream;
      $("#cam-wrap").hidden = false;
      $("#meal-preview").hidden = true;
    }).catch(function () {
      flash("meal-flash", "Camera unavailable or permission denied — use the file picker.", "err");
      $("#meal-photo").click();
    });
  }
  function closeCam() {
    if (camStream) { camStream.getTracks().forEach(function (t) { t.stop(); }); camStream = null; }
    var w = $("#cam-wrap"); if (w) w.hidden = true;
  }
  function snapCam() {
    var v = $("#cam-video");
    if (!v.videoWidth) return;
    var cv = document.createElement("canvas");
    cv.width = v.videoWidth; cv.height = v.videoHeight;
    cv.getContext("2d").drawImage(v, 0, 0);
    cv.toBlob(function (b) {
      camBlob = b;
      $("#meal-photo").value = "";          // camera shot wins over any picked file
      var p = $("#meal-preview");
      p.src = URL.createObjectURL(b); p.hidden = false;
      closeCam();
      flash("meal-flash", "Photo captured — add notes and hit Analyze.", "ok");
    }, "image/jpeg", 0.9);
  }

  /* ---------------- activity ---------------- */
  function loadActivity() {
    Promise.all([api.activities(), api.entries()]).then(function (res) {
      var acts = res[0], entries = res[1];
      // 7-day movement aggregates (deep view — Today tab covers today)
      var cutoff = isoPlus(-6);
      var week = entries.filter(function (e) {
        return e.date >= cutoff && (e.steps != null || e.active_calories != null);
      });
      function aggr(k, mode) {
        var vals = week.map(function (e) { return e[k]; }).filter(function (v) { return v != null; });
        if (!vals.length) return null;
        var s = vals.reduce(function (a, b) { return a + b; }, 0);
        return mode === "sum" ? s : Math.round(s / vals.length);
      }
      var row = $("#movement-stats"); row.innerHTML = "";
      function mt(label, v, unit, sub) { row.appendChild(statTile(label, v == null ? "—" : v, unit || "", sub || "")); }
      var steps7 = aggr("steps");
      mt("Avg steps / day", steps7 == null ? null : steps7.toLocaleString(), "", week.length + " days of data");
      var dist7 = aggr("daily_distance_m", "sum");
      mt("Distance · 7d", dist7 == null ? null : (dist7 / 1000).toFixed(1), " km");
      mt("Active kcal · 7d", aggr("active_calories", "sum"), " kcal");
      var intens = week.map(function (e) { return (e.intensity_moderate_min || 0) + 2 * (e.intensity_vigorous_min || 0); })
                       .reduce(function (a, b) { return a + b; }, 0);
      mt("Intensity min · 7d", week.length ? intens : null, "", "WHO target 150+");
      mt("Floors / day", aggr("floors_climbed"), "");

      // workouts table
      var body = $("#activity-body"); body.innerHTML = "";
      $("#activity-empty").style.display = acts.length ? "none" : "";
      acts.slice().reverse().forEach(function (a) {
        body.appendChild(el("tr", {}, [
          el("td", { class: "date", text: a.date ? fmtDate(a.date) : "—" }),
          el("td", { text: (a.type || "").replace(/_/g, " ") }),
          el("td", { text: a.name || "—" }),
          el("td", { class: "num", text: a.duration_min == null ? "—" : a.duration_min + " min" }),
          el("td", { class: "num", text: a.distance_m == null ? "—" : (a.distance_m / 1000).toFixed(2) + " km" }),
          el("td", { class: "num", text: a.calories == null ? "—" : a.calories }),
          el("td", { class: "num", text: a.avg_hr == null ? "—" : a.avg_hr }),
          el("td", { class: "num", text: a.training_load == null ? "—" : Math.round(a.training_load) })
        ]));
      });
      // charts from daily entries (all-day), not just workouts
      var steps = entries.filter(function (e) { return e.steps != null; }).slice(-30).map(function (e) { return { date: e.date, v: e.steps }; });
      barChart("chart-steps", dietCharts, steps, "");
      var acal = entries.filter(function (e) { return e.active_calories != null; }).slice(-30).map(function (e) { return { date: e.date, v: e.active_calories }; });
      barChart("chart-activeCal", dietCharts, acal, "kcal");
    }).catch(showErr);
  }

  /* ---------------- body ---------------- */
  function loadBody() {
    Promise.all([api.entries(), api.diet(todayISO())]).then(function (res) {
      var entries = res[0], d = res[1];
      var latest = function (k) { for (var i = entries.length - 1; i >= 0; i--) if (entries[i][k] != null) return entries[i][k]; return null; };
      var row = $("#body-stats"); row.innerHTML = "";
      var goalW = d.targets ? d.targets.weight_goal : null;
      var ws = (d.weight_series || []).filter(function (w) { return w.date >= isoPlus(-30); });
      var d30 = ws.length >= 2 ? Math.round((ws[ws.length - 1].weight - ws[0].weight) * 10) / 10 : null;
      var rvals = entries.filter(function (e) { return e.date >= isoPlus(-6) && e.training_readiness != null; })
                         .map(function (e) { return e.training_readiness; });
      var r7 = rvals.length ? Math.round(rvals.reduce(function (a, b) { return a + b; }, 0) / rvals.length) : null;
      row.appendChild(statTile("Weight", d.weight == null ? "—" : d.weight, " " + d.weight_unit, goalW ? "goal " + goalW : ""));
      row.appendChild(statTile("30-day change", d30 == null ? "—" : (d30 > 0 ? "+" : "") + d30, " " + d.weight_unit, ""));
      row.appendChild(statTile("VO₂max", latest("vo2max") || "—", "", ""));
      row.appendChild(statTile("Readiness · 7d avg", r7 == null ? "—" : r7, "", "latest " + (latest("training_readiness") || "—")));
      row.appendChild(statTile("Body fat", latest("body_fat_pct") == null ? "—" : latest("body_fat_pct"), "%", ""));
      lineChart("chart-bodyWeight", dietCharts, (d.weight_series || []).map(function (w) { return { date: w.date, v: w.weight }; }), "Weight", COLORS.eff, goalW);
      var rr = entries.filter(function (e) { return e.training_readiness != null; }).slice(-30).map(function (e) { return { date: e.date, v: e.training_readiness }; });
      barChart("chart-readiness", dietCharts, rr, "");
    }).catch(showErr);
  }

  /* ---------------- insights ---------------- */
  function loadInsights() {
    api.insights().then(function (d) {
      renderFactorList($("#insight-sleep"), d.sleep_factors, "Log a couple of weeks across diet, activity and sleep to see what moves your sleep.");
      renderFactorList($("#insight-nextday"), d.next_day_factors, "Not enough paired days yet.");
    }).catch(showErr);
    var today = todayISO();
    api.summary(today, false).then(function (s) {
      $("#daily-summary").innerHTML = s.summary ? s.summary.replace(/\n/g, "<br>") : "";
    }).catch(function () { $("#daily-summary").textContent = "Add an API key (Diet tab) to generate a daily summary."; });
  }
  function renderFactorList(ul, items, emptyMsg) {
    ul.innerHTML = "";
    var rows = (items || []).filter(function (f) { return f.strength !== "negligible"; });
    if (!rows.length) { ul.appendChild(el("li", { class: "factor empty-factor", text: emptyMsg })); return; }
    rows.slice(0, 8).forEach(function (f) {
      ul.appendChild(el("li", { class: "factor" }, [
        el("span", { class: "factor-chip " + (f.r > 0 ? "up" : "down"), text: (f.r > 0 ? "+" : "") + f.r.toFixed(2) }),
        el("div", { class: "factor-body" }, [
          el("div", { class: "factor-msg", text: f.message || f.label }),
          f.action ? el("div", { class: "factor-action", text: f.action }) : null
        ])
      ]));
    });
  }

  /* ---------------- small chart helpers ---------------- */
  function lineChart(canvasId, store, series, label, color, goal) {
    var ctx = $("#" + canvasId); if (!ctx) return;
    if (store[canvasId]) store[canvasId].destroy();
    var labels = series.map(function (s) { return fmtDate(s.date, { month: "short", day: "numeric" }); });
    var ds = [{ label: label, data: series.map(function (s) { return s.v; }), borderColor: color, backgroundColor: COLORS.effFill, borderWidth: 2.5, fill: true, tension: 0.3, pointRadius: 0 }];
    if (goal) ds.push({ label: "goal", data: series.map(function () { return goal; }), borderColor: COLORS.target, borderWidth: 1.5, borderDash: [5, 5], pointRadius: 0, fill: false });
    store[canvasId] = new Chart(ctx, { type: "line", data: { labels: labels, datasets: ds }, options: chartOpts(baseScales("", undefined), function (c) { return c.parsed.y; }) });
  }
  function barChart(canvasId, store, series, unit) {
    var ctx = $("#" + canvasId); if (!ctx) return;
    if (store[canvasId]) store[canvasId].destroy();
    var labels = series.map(function (s) { return fmtDate(s.date, { month: "short", day: "numeric" }); });
    store[canvasId] = new Chart(ctx, {
      type: "bar",
      data: { labels: labels, datasets: [{ data: series.map(function (s) { return s.v; }), backgroundColor: COLORS.eff, borderRadius: 4, maxBarThickness: 18 }] },
      options: chartOpts(baseScales(unit || "", undefined), function (c) { return c.parsed.y + (unit || ""); })
    });
  }

  function _try(fn) { try { fn(); } catch (e) { console.error("init block failed:", e); } }

  function init() {
    // Tabs + sync FIRST — navigation must work even if another block fails.
    _try(function () {
      document.querySelectorAll(".tabbtn").forEach(function (b) {
        b.addEventListener("click", function () { showTab(b.getAttribute("data-tab")); });
      });
      window.addEventListener("hashchange", function () { showTab(currentTab()); });
      showTab(currentTab());
    });
    _try(function () { $("#sync-btn").addEventListener("click", onSync); });
    _try(function () {
      if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
    });

    // sleep check-in
    _try(function () {
      api.getWake().then(function (s) {
        var w = (s && s.wake) || "06:30";
        $("#wake-setting").value = w;
      });
      $("#for-tonight").addEventListener("click", function () { applyCheckinMode("tonight"); });
      $("#for-lastnight").addEventListener("click", function () { applyCheckinMode("lastnight"); });
      $("#for-custom").addEventListener("click", function () { applyCheckinMode("custom"); });
      $("#form-date").addEventListener("input", function () {
        checkinMode = "custom";
        ["tonight", "lastnight", "custom"].forEach(function (m) {
          $("#for-" + m).classList.toggle("seg-on", m === "custom");
        });
      });
      applyCheckinMode("tonight");
      $("#checkin-form").addEventListener("submit", onSubmit);
      $("#wake-form").addEventListener("submit", onSaveWake);
    });

    // diet + settings + insights handlers
    _try(function () {
      $("#meal-form").addEventListener("submit", onMealSubmit);
      $("#meal-photo").addEventListener("change", function () {
        var f = this.files[0], p = $("#meal-preview");
        camBlob = null;                      // picked file wins over an old snap
        if (f) { p.src = URL.createObjectURL(f); p.hidden = false; } else { p.hidden = true; }
      });
      $("#cam-open").addEventListener("click", openCam);
      $("#cam-snap").addEventListener("click", snapCam);
      $("#cam-cancel").addEventListener("click", closeCam);
      $("#diet-prev").addEventListener("click", function () { dietDate = addDaysISO(dietDate || todayISO(), -1); loadDiet(); });
      $("#diet-next").addEventListener("click", function () { if ((dietDate || todayISO()) < todayISO()) { dietDate = addDaysISO(dietDate, 1); loadDiet(); } });
      $("#diet-date-sel").addEventListener("change", function () { if (this.value) { dietDate = this.value; loadDiet(); } });
      $("#goals-form").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var s = { weight_unit: $("#g-unit").value, weight_goal: $("#g-weight").value, weight_pace: $("#g-pace").value };
        api.setSettings(s).then(function () { flash("goals-flash", "Saved — targets recomputed.", "ok"); loadDiet(); }).catch(showErr);
      });
      $("#weight-form").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var w = $("#w-val").value;
        if (w === "") return;
        api.logWeight({ date: $("#w-date").value || todayISO(), weight: w }).then(function () {
          flash("weight-flash", "Saved.", "ok");
          $("#w-val").value = "";
          loadDiet();
        }).catch(function (e) { flash("weight-flash", "Failed: " + (e.message || e), "err"); });
      });
      $("#today-summary-refresh").addEventListener("click", function () {
        aiBusy("Writing your daily summary…");
        $("#today-summary").textContent = "Generating…";
        api.summary(todayISO(), true).then(function (s) { aiDone(); $("#today-summary").innerHTML = (s.summary || "").replace(/\n/g, "<br>"); }).catch(function (e) { aiDone(); showErr(e); });
      });
      $("#key-form").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var k = $("#api-key").value.trim(); if (!k) return;
        api.setKey(k).then(function () { flash("key-flash", "Key saved.", "ok"); $("#api-key").value = ""; $("#key-state").textContent = "✓ key saved"; }).catch(showErr);
      });
      $("#summary-refresh").addEventListener("click", function () {
        aiBusy("Writing your daily summary…");
        $("#daily-summary").textContent = "Generating…";
        api.summary(todayISO(), true).then(function (s) { aiDone(); $("#daily-summary").innerHTML = (s.summary || "").replace(/\n/g, "<br>"); }).catch(function (e) { aiDone(); showErr(e); });
      });
    });

    refresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
