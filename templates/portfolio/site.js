/* Renders data.json into index.html. No build step. Preview locally with: python -m http.server */
(function () {
  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function section(title) {
    var s = el("section"); s.appendChild(el("h2", "", title)); $("main").appendChild(s); return s;
  }
  function bullets(list) {
    var ul = el("ul");
    (list || []).forEach(function (b) { ul.appendChild(el("li", "", String(b).replace(/\*\*/g, ""))); });
    return ul;
  }
  function render(d) {
    document.title = d.name + (d.headline ? " | " + d.headline : "");
    $("name").textContent = d.name;
    $("headline").textContent = d.headline || "";
    var c = d.contact || {};
    [["Email", c.email ? "mailto:" + c.email : ""], ["LinkedIn", c.linkedin], ["GitHub", c.github], ["Website", c.website], ["Resume (PDF)", d.resume_pdf || "resume.pdf"]]
      .forEach(function (p) {
        if (!p[1]) return;
        var a = el("a", "btn", p[0]); a.href = /^(https?:|mailto:)/.test(p[1]) || p[1] === (d.resume_pdf || "resume.pdf") ? p[1] : "https://" + p[1];
        $("links").appendChild(a);
      });
    var t = el("button", "btn", "Light / dark"); t.type = "button";
    t.onclick = function () {
      var r = document.documentElement, dark = getComputedStyle(r).getPropertyValue("--bg").trim() === "#0f141b";
      r.setAttribute("data-theme", dark ? "light" : "dark");
    };
    $("links").appendChild(t);
    if (d.summary) { section("About").appendChild(el("p", "", d.summary.replace(/\*\*/g, ""))); }
    var sk = d.skills || {};
    if (Object.keys(sk).length) {
      var s = section("Skills");
      Object.keys(sk).forEach(function (k) {
        var card = el("div", "card"); card.appendChild(el("strong", "", k));
        var tags = el("div", "tags"); (sk[k] || []).forEach(function (x) { tags.appendChild(el("span", "tag", x)); });
        card.appendChild(tags); s.appendChild(card);
      });
    }
    if ((d.experience || []).length) {
      var x = section("Experience");
      d.experience.forEach(function (e) {
        var card = el("div", "card"), row = el("div", "row");
        row.appendChild(el("strong", "", e.title + " | " + e.company));
        row.appendChild(el("span", "muted", e.dates || ""));
        card.appendChild(row); card.appendChild(bullets(e.bullets)); x.appendChild(card);
      });
    }
    if ((d.projects || []).length) {
      var p = section("Projects");
      d.projects.forEach(function (pr) {
        var card = el("div", "card");
        if (typeof pr === "string") { card.textContent = pr.replace(/\*\*/g, ""); }
        else {
          card.appendChild(el("strong", "", pr.name || ""));
          card.appendChild(el("p", "", pr.description || ""));
          if (pr.url) { var a = el("a", "", pr.url); a.href = pr.url; card.appendChild(a); }
        }
        p.appendChild(card);
      });
    }
    var more = (d.certifications || []).concat((d.education || []).map(function (e) { return e.degree + ", " + e.school; }));
    if (more.length) { section("Certifications and education").appendChild(bullets(more)); }
  }
  fetch("data.json").then(function (r) { return r.json(); }).then(render).catch(function () {
    $("main").appendChild(el("p", "", "Could not load data.json. Serve this folder over http (python -m http.server)."));
  });
})();
