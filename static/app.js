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
    delEntry: function (d) { return send("DELETE", "/api/entries/" + encodeURIComponent(d)); }
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
      flash("form-flash", "Check-in saved \u2014 merged onto that night.", "ok");
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

  function init() {
    // default form values
    $("#form-date").value = todayISO();
    api.getWake().then(function (s) {
      var w = (s && s.wake) || "06:30";
      $("#wake-setting").value = w;
    });
    $("#checkin-form").addEventListener("submit", onSubmit);
    $("#wake-form").addEventListener("submit", onSaveWake);
    refresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
