(function () {
  "use strict";

  var FILTERS = ["all", "tech", "ai", "crypto", "x"];
  var READ_KEY = "newsfeed-read";
  var FILTER_KEY = "newsfeed-filter";
  var THEME_KEY = "newsfeed-theme";
  var root = document.documentElement;
  var items = Array.prototype.slice.call(document.querySelectorAll(".item"));
  var byId = {};
  items.forEach(function (el) { byId[el.dataset.id] = el; });

  function loadRead() {
    try {
      var raw = JSON.parse(localStorage.getItem(READ_KEY) || "{}");
      return raw && typeof raw === "object" ? raw : {};
    } catch (err) {
      return {};
    }
  }

  function saveRead(read) {
    var now = Date.now();
    var kept = {};
    Object.keys(read).forEach(function (id) {
      if (byId[id] || now - read[id] < 14 * 86400000) kept[id] = read[id];
    });
    try { localStorage.setItem(READ_KEY, JSON.stringify(kept)); } catch (err) {}
    return kept;
  }

  var read = loadRead();
  items.forEach(function (el) {
    if (read[el.dataset.id]) el.classList.add("is-read");
    var details = el.querySelector("details");
    if (!details) return;
    details.addEventListener("toggle", function () {
      if (!details.open) return;
      read[el.dataset.id] = Date.now();
      el.classList.add("is-read");
      read = saveRead(read);
      updateTitle();
    });
  });

  function matches(el, filter) {
    if (filter === "all") return true;
    if (filter === "x") return el.dataset.kind === "x";
    var topics = (el.dataset.topics || "").split(/\s+/);
    return topics.indexOf(filter) !== -1;
  }

  function updateCounts() {
    FILTERS.forEach(function (filter) {
      var count = items.filter(function (el) { return matches(el, filter); }).length;
      var slot = document.querySelector('[data-count="' + filter + '"]');
      if (slot) slot.textContent = String(count);
    });
  }

  function setFilter(filter) {
    if (FILTERS.indexOf(filter) === -1) filter = "all";
    var visible = 0;
    items.forEach(function (el) {
      var show = matches(el, filter);
      el.classList.toggle("is-hidden", !show);
      if (show) visible += 1;
    });
    document.querySelectorAll("[data-filter]").forEach(function (button) {
      button.setAttribute("aria-selected", button.dataset.filter === filter ? "true" : "false");
    });
    var empty = document.getElementById("empty");
    if (empty) empty.hidden = visible !== 0;
    try { localStorage.setItem(FILTER_KEY, filter); } catch (err) {}
  }

  document.querySelectorAll("[data-filter]").forEach(function (button) {
    button.addEventListener("click", function () {
      var filter = button.dataset.filter;
      setFilter(filter);
      if (filter === "all") {
        history.replaceState(null, "", location.pathname + location.search);
      } else {
        history.replaceState(null, "", "#" + filter);
      }
    });
  });

  var initial = (location.hash || "").replace(/^#/, "");
  if (FILTERS.indexOf(initial) === -1) {
    try { initial = localStorage.getItem(FILTER_KEY) || "all"; } catch (err) { initial = "all"; }
  }
  setFilter(initial);
  updateCounts();

  document.getElementById("mark-read").addEventListener("click", function () {
    var now = Date.now();
    items.forEach(function (el) {
      read[el.dataset.id] = now;
      el.classList.add("is-read");
    });
    read = saveRead(read);
    updateTitle();
  });

  function unreadCount() {
    return items.filter(function (el) { return !el.classList.contains("is-read"); }).length;
  }

  function updateTitle() {
    var count = unreadCount();
    document.title = count ? "Feed (" + count + ")" : "Feed";
  }
  updateTitle();

  function rel(iso) {
    var then = new Date(iso).getTime();
    if (!then) return "";
    var seconds = Math.round((Date.now() - then) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
    var days = Math.floor(seconds / 86400);
    return days === 1 ? "yesterday" : days + "d ago";
  }

  function refreshTimes() {
    document.querySelectorAll("time[datetime]").forEach(function (node) {
      node.textContent = rel(node.getAttribute("datetime"));
    });
  }
  refreshTimes();
  setInterval(refreshTimes, 60000);

  function explicitTheme() {
    var set = root.getAttribute("data-theme");
    return set === "light" || set === "dark" ? set : "";
  }

  function currentTheme() {
    var set = explicitTheme();
    if (set) return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyThemeLabel() {
    var button = document.getElementById("theme");
    var next = currentTheme() === "dark" ? "Light" : "Dark";
    button.textContent = next;
    button.setAttribute("aria-label", "Switch to " + next.toLowerCase() + " mode");
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", currentTheme() === "dark" ? "#12110f" : "#f6f3ec");
  }

  document.getElementById("theme").addEventListener("click", function () {
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(THEME_KEY, next); } catch (err) {}
    applyThemeLabel();
  });
  applyThemeLabel();

  var media = window.matchMedia("(prefers-color-scheme: dark)");
  if (media.addEventListener) {
    media.addEventListener("change", function () {
      if (!explicitTheme()) applyThemeLabel();
    });
  }
})();
