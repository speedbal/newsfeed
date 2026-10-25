(function () {
  "use strict";

  var FILTERS = ["all", "tech", "ai", "crypto", "x"];
  var READ_KEY = "newsfeed-read";
  var FILTER_KEY = "newsfeed-filter";
  var THEME_KEY = "newsfeed-theme";
  var LONG = 400;
  var root = document.documentElement;
  var items = Array.prototype.slice.call(document.querySelectorAll(".item"));
  var byId = {};
  var wideQuery = window.matchMedia("(min-width: 1080px)");
  var motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  var reader = document.getElementById("reader");
  var readerEmpty = document.getElementById("reader-empty");
  var readerStage = document.getElementById("reader-stage");
  var readerClose = document.getElementById("reader-close");
  var selected = null;
  var currentFilter = "all";

  items.forEach(function (el) { byId[el.dataset.id] = el; });

  function isWide() { return wideQuery.matches; }

  function hashValue() {
    var raw = (location.hash || "").replace(/^#/, "");
    try { raw = decodeURIComponent(raw); } catch (err) {}
    return raw;
  }

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

  function markRead(el) {
    if (!el) return;
    read[el.dataset.id] = Date.now();
    el.classList.add("is-read");
    read = saveRead(read);
    updateTitle();
  }

  function matches(el, filter) {
    if (filter === "all") return true;
    if (filter === "x") return el.dataset.kind === "x";
    var topics = (el.dataset.topics || "").split(/\s+/);
    return topics.indexOf(filter) !== -1;
  }

  function visibleItems() {
    return items.filter(function (el) { return !el.classList.contains("is-hidden"); });
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
    currentFilter = filter;
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

  function syncHash() {
    var next = location.pathname + location.search;
    if (selected) next += "#" + selected.dataset.id;
    else if (currentFilter !== "all") next += "#" + currentFilter;
    var current = location.pathname + location.search + location.hash;
    if (current !== next) history.replaceState(null, "", next);
  }

  function fillReader(el) {
    readerStage.replaceChildren();
    if (el.dataset.kind === "x") {
      var tweet = el.querySelector(".tweet");
      if (!tweet) return;
      var clone = tweet.cloneNode(true);
      clone.classList.add("in-reader");
      var clipped = clone.querySelector(".tweet-text");
      if (clipped) clipped.classList.remove("is-clipped");
      var more = clone.querySelector(".more");
      if (more) more.remove();
      readerStage.appendChild(clone);
      return;
    }
    var article = document.createElement("article");
    article.className = "reader-article";
    var head = document.createElement("div");
    head.className = "reader-kicker";
    var kicker = el.querySelector(".kicker");
    var time = el.querySelector("time");
    if (kicker) head.appendChild(kicker.cloneNode(true));
    if (time) head.appendChild(time.cloneNode(true));
    article.appendChild(head);
    var title = el.querySelector(".title");
    if (title) {
      var heading = document.createElement("h2");
      heading.className = "reader-title";
      heading.textContent = title.textContent;
      article.appendChild(heading);
    }
    var meta = el.querySelector(".meta");
    if (meta) {
      var line = document.createElement("p");
      line.className = "reader-meta";
      line.textContent = meta.textContent;
      article.appendChild(line);
    }
    var content = el.querySelector(".content");
    var outbound = el.querySelector(".outbound");
    if (content) article.appendChild(content.cloneNode(true));
    if (outbound) article.appendChild(outbound.cloneNode(true));
    readerStage.appendChild(article);
  }

  function showReader(el) {
    fillReader(el);
    readerEmpty.hidden = true;
    readerStage.hidden = false;
    readerClose.hidden = false;
    reader.scrollTop = 0;
  }

  function hideReader() {
    readerStage.replaceChildren();
    readerStage.hidden = true;
    readerClose.hidden = true;
    readerEmpty.hidden = false;
  }

  function clearSelection(opts) {
    opts = opts || {};
    if (selected) {
      selected.classList.remove("is-selected");
      selected.removeAttribute("aria-current");
    }
    selected = null;
    if (isWide()) hideReader();
    if (!opts.keepHash) syncHash();
  }

  function selectItem(el, opts) {
    opts = opts || {};
    if (!el || el.classList.contains("is-hidden")) return;
    if (selected && selected !== el) {
      selected.classList.remove("is-selected");
      selected.removeAttribute("aria-current");
    }
    selected = el;
    el.classList.add("is-selected");
    el.setAttribute("aria-current", "true");
    markRead(el);
    if (isWide()) {
      var details = el.querySelector("details");
      if (details) details.open = false;
      showReader(el);
      if (!opts.skipFocus) reader.focus({ preventScroll: true });
    }
    if (!opts.skipScroll) {
      el.scrollIntoView({ block: "nearest", behavior: motionQuery.matches ? "auto" : "smooth" });
    }
    if (!opts.keepHash) syncHash();
  }

  function clipTweets() {
    items.forEach(function (el) {
      if (el.dataset.kind !== "x") return;
      var count = parseInt(el.dataset.chars || "0", 10);
      if (count <= LONG) return;
      var text = el.querySelector(".tweet-text");
      var more = el.querySelector(".more");
      if (!text || !more) return;
      text.classList.add("is-clipped");
      more.hidden = false;
      more.addEventListener("click", function (event) {
        event.stopPropagation();
        var clipped = text.classList.toggle("is-clipped");
        more.textContent = clipped ? "Show more" : "Show less";
        markRead(el);
      });
    });
  }

  items.forEach(function (el) {
    if (read[el.dataset.id]) el.classList.add("is-read");
    var summary = el.querySelector("summary");
    var details = el.querySelector("details");
    if (summary) {
      summary.addEventListener("click", function (event) {
        if (!isWide()) return;
        event.preventDefault();
        selectItem(el);
      });
    }
    if (details) {
      details.addEventListener("toggle", function () {
        if (isWide()) {
          if (details.open) details.open = false;
          return;
        }
        if (!details.open) {
          if (selected === el) {
            el.classList.remove("is-selected");
            el.removeAttribute("aria-current");
            selected = null;
            syncHash();
          }
          return;
        }
        if (selected && selected !== el) {
          selected.classList.remove("is-selected");
          selected.removeAttribute("aria-current");
        }
        selected = el;
        el.classList.add("is-selected");
        el.setAttribute("aria-current", "true");
        markRead(el);
        syncHash();
        el.scrollIntoView({ block: "start", behavior: motionQuery.matches ? "auto" : "smooth" });
      });
    }
    if (el.dataset.kind === "x") {
      el.addEventListener("click", function (event) {
        if (!isWide()) return;
        if (event.target.closest("a, button")) return;
        selectItem(el);
      });
    }
  });

  clipTweets();

  document.querySelectorAll("[data-filter]").forEach(function (button) {
    button.addEventListener("click", function () {
      var filter = button.dataset.filter;
      setFilter(filter);
      if (selected && !matches(selected, filter)) clearSelection({ keepHash: true });
      syncHash();
    });
  });

  function openFromHash() {
    var initial = hashValue();
    var filter = "all";
    if (FILTERS.indexOf(initial) !== -1) filter = initial;
    else {
      try { filter = localStorage.getItem(FILTER_KEY) || "all"; } catch (err) { filter = "all"; }
    }
    var target = byId[initial] || null;
    if (target && !matches(target, filter)) filter = "all";
    setFilter(filter);
    if (!target) return;
    if (isWide()) {
      selectItem(target, { keepHash: true, skipScroll: false });
      return;
    }
    var details = target.querySelector("details");
    if (details) details.open = true;
    else selectItem(target, { keepHash: true });
  }

  openFromHash();
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

  readerClose.addEventListener("click", function () { clearSelection(); });

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

  function move(delta) {
    var list = visibleItems();
    if (!list.length) return;
    var index = selected ? list.indexOf(selected) : -1;
    if (index === -1) index = delta > 0 ? -1 : list.length;
    var next = index + delta;
    if (next < 0) next = 0;
    if (next >= list.length) next = list.length - 1;
    if (isWide()) selectItem(list[next]);
    else {
      if (selected && selected !== list[next]) {
        selected.classList.remove("is-selected");
        selected.removeAttribute("aria-current");
      }
      selected = list[next];
      selected.classList.add("is-selected");
      selected.setAttribute("aria-current", "true");
      selected.scrollIntoView({ block: "nearest" });
    }
  }

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    var target = event.target;
    if (target && target.closest && target.closest("input, textarea")) return;
    var key = event.key;
    if (key === "j" || key === "k") {
      event.preventDefault();
      move(key === "j" ? 1 : -1);
      return;
    }
    if (key === "ArrowDown" || key === "ArrowUp") {
      if (isWide() && document.activeElement === reader) {
        var dir = key === "ArrowDown" ? 1 : -1;
        var max = reader.scrollHeight - reader.clientHeight;
        var atEnd = dir > 0 ? reader.scrollTop >= max - 1 : reader.scrollTop <= 0;
        if (!atEnd) {
          reader.scrollTop = Math.max(0, Math.min(max, reader.scrollTop + dir * 72));
          event.preventDefault();
          return;
        }
      }
      event.preventDefault();
      move(key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (key === "Enter" && !isWide() && selected && target === document.body) {
      var details = selected.querySelector("details");
      if (details) {
        details.open = !details.open;
        event.preventDefault();
      }
      return;
    }
    if (key === "Escape") {
      if (isWide() && selected) {
        clearSelection();
        event.preventDefault();
        return;
      }
      if (selected) {
        var open = selected.querySelector("details");
        if (open && open.open) open.open = false;
      }
    }
  });

  function onWideChange() {
    if (isWide()) {
      items.forEach(function (el) {
        var details = el.querySelector("details");
        if (details && details.open) details.open = false;
      });
      if (selected) selectItem(selected, { keepHash: true, skipScroll: true });
      else hideReader();
      return;
    }
    hideReader();
  }

  if (wideQuery.addEventListener) wideQuery.addEventListener("change", onWideChange);

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
