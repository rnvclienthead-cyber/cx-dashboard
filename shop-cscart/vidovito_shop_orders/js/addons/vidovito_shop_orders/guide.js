(function () {
  if (window.__vidovitoGuideBound) return;
  window.__vidovitoGuideBound = true;

  try {
    var root = document.getElementById("vidovito-guide");
    if (!root) return;
    var slides = root.querySelectorAll(".ty-vidovito-guide__slides figure");
    var prev = root.querySelector("[data-vidovito-prev]");
    var next = root.querySelector("[data-vidovito-next]");
    var dotsWrap = root.querySelector("[data-vidovito-dots]");
    var index = 0;

    function show(i) {
      if (!slides.length) return;
      index = (i + slides.length) % slides.length;
      for (var n = 0; n < slides.length; n++) {
        slides[n].classList.toggle("is-on", n === index);
      }
      var dots = dotsWrap ? dotsWrap.querySelectorAll("button") : [];
      for (var d = 0; d < dots.length; d++) dots[d].classList.toggle("is-on", d === index);
      if (prev) prev.disabled = index === 0;
      if (next) next.disabled = index === slides.length - 1;
    }

    if (dotsWrap) {
      for (var s = 0; s < slides.length; s++) {
        var dot = document.createElement("button");
        dot.type = "button";
        dot.setAttribute("aria-label", "Шаг " + (s + 1));
        dot.setAttribute("data-i", String(s));
        dotsWrap.appendChild(dot);
      }
      dotsWrap.addEventListener("click", function (e) {
        var btn = e.target.closest("button");
        if (!btn) return;
        show(parseInt(btn.getAttribute("data-i"), 10) || 0);
      });
    }

    function openGuide(e) {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      show(0);
      root.hidden = false;
      document.body.style.overflow = "hidden";
    }

    function closeGuide() {
      root.hidden = true;
      document.body.style.overflow = "";
    }

    if (prev) prev.addEventListener("click", function () { show(index - 1); });
    if (next) next.addEventListener("click", function () { show(index + 1); });
    root.addEventListener("click", function (e) {
      if (e.target.closest("[data-vidovito-close]")) closeGuide();
    });
    document.addEventListener("keydown", function (e) {
      if (root.hidden) return;
      if (e.key === "Escape") closeGuide();
      if (e.key === "ArrowRight") show(index + 1);
      if (e.key === "ArrowLeft") show(index - 1);
    });

    var startX = 0;
    var stage = root.querySelector(".ty-vidovito-guide__slides");
    if (stage) {
      stage.addEventListener("touchstart", function (e) {
        startX = e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : 0;
      }, { passive: true });
      stage.addEventListener("touchend", function (e) {
        var x = e.changedTouches && e.changedTouches[0] ? e.changedTouches[0].clientX : startX;
        var dx = x - startX;
        if (dx > 40) show(index - 1);
        if (dx < -40) show(index + 1);
      }, { passive: true });
    }

    var own = document.querySelector(".ty-vidovito-guide-open");
    if (own) own.addEventListener("click", openGuide);

    var foreign = false;
    var nodes = document.querySelectorAll("a, button, summary, [role='button']");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.getAttribute("data-vidovito-own") === "1") continue;
      if (root.contains(el)) continue;
      var text = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!/информац\S*\s+(по|о)\s+доставке/i.test(text)) continue;
      foreign = true;
      el.addEventListener("click", openGuide);
    }
    if (foreign && own) own.style.display = "none";

    show(0);
  } catch (err) {}
})();
