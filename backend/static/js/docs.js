/* API reference: highlight the section currently in view and scroll smoothly
   to it when a sidebar link is clicked. */

(function () {
  "use strict";

  const links = Array.from(document.querySelectorAll(".docs-nav a"));
  const sections = links
    .map(function (link) { return document.querySelector(link.getAttribute("href")); })
    .filter(Boolean);

  if (!sections.length) return;

  function setActive(id) {
    links.forEach(function (link) {
      link.classList.toggle("is-active", link.getAttribute("href") === "#" + id);
    });
  }

  // Bias the observation band towards the top of the viewport so the heading
  // you are reading is the one highlighted, not the one halfway down.
  const observer = new IntersectionObserver(
    function (entries) {
      const visible = entries.filter(function (entry) { return entry.isIntersecting; });
      if (visible.length) setActive(visible[0].target.id);
    },
    { rootMargin: "-80px 0px -65% 0px", threshold: 0 }
  );

  sections.forEach(function (section) { observer.observe(section); });
  setActive(sections[0].id);

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  links.forEach(function (link) {
    link.addEventListener("click", function (event) {
      const target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reduced.matches ? "auto" : "smooth", block: "start" });
      history.replaceState(null, "", link.getAttribute("href"));
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
    });
  });
})();
