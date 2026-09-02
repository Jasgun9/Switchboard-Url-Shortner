/* Sidebar for the API reference: highlight whatever section you're looking at,
   and scroll to it when a link is clicked. */

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

  // Band is biased towards the top of the viewport, otherwise the highlighted
  // heading is the one halfway down the screen rather than the one you're on.
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
