/* Shared UI behaviour: entrance, nav, dropdowns, dialogs, copy, disclosure,
   password reveal, form loading states.
   GSAP handles sequenced entrances; Motion handles one-off element states. */

(function () {
  "use strict";

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const still = () => reduced.matches;

  /* --- page entrance -----------------------------------------------------
     Blocks already on screen fade up in one staggered run. Blocks further down
     wait until they are scrolled to — holding the whole page at opacity 0 would
     leave a visitor who scrolls immediately looking at empty sections. */

  function entrance() {
    const targets = Array.from(document.querySelectorAll("main [data-animate]"));

    // Releasing the class and setting the from-state happen in the same task,
    // so the browser never paints the elements in between.
    document.documentElement.classList.remove("preanimate");

    if (!targets.length || still() || !window.gsap) return;

    const fold = window.innerHeight;
    const visible = [];
    const deferred = [];
    targets.forEach(function (el) {
      (el.getBoundingClientRect().top < fold ? visible : deferred).push(el);
    });

    gsap.fromTo(
      visible,
      { opacity: 0, y: 20, scale: 0.985 },
      {
        opacity: 1,
        y: 0,
        scale: 1,
        duration: 0.55,
        ease: "power3.out",
        stagger: 0.09,
        clearProps: "transform,opacity",
      }
    );

    if (!deferred.length) return;

    if (!window.Motion) {
      gsap.set(deferred, { clearProps: "opacity" });
      return;
    }

    deferred.forEach(function (el) {
      gsap.set(el, { opacity: 0, y: 24 });
      Motion.inView(
        el,
        function () {
          gsap.to(el, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out", clearProps: "transform,opacity" });
        },
        { margin: "0px 0px -12% 0px" }
      );
    });
  }

  /* --- mobile navigation ------------------------------------------------- */

  function mobileNav() {
    const toggle = document.querySelector("[data-nav-toggle]");
    const panel = document.querySelector("[data-nav-panel]");
    if (!toggle || !panel) return;

    toggle.addEventListener("click", function () {
      const open = panel.dataset.open !== "true";
      panel.dataset.open = String(open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.querySelector("use").setAttribute("href", open ? "#i-close" : "#i-menu");

      if (open && window.Motion && !still()) {
        Motion.animate(panel, { opacity: [0, 1], y: [-6, 0] }, { duration: 0.28 });
      }
    });
  }

  /* --- dropdowns ---------------------------------------------------------
     One open at a time, closed by Escape or an outside click. */

  function closeDropdown(root) {
    const menu = root.querySelector("[data-dropdown-menu]");
    const trigger = root.querySelector("[data-dropdown-trigger]");
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }

  function dropdowns() {
    const roots = document.querySelectorAll("[data-dropdown]");
    if (!roots.length) return;

    roots.forEach(function (root) {
      const trigger = root.querySelector("[data-dropdown-trigger]");
      const menu = root.querySelector("[data-dropdown-menu]");

      trigger.addEventListener("click", function (event) {
        event.stopPropagation();
        const opening = menu.hidden;
        roots.forEach(closeDropdown);
        if (!opening) return;

        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        if (window.Motion && !still()) {
          Motion.animate(menu, { opacity: [0, 1], scale: [0.97, 1], y: [-4, 0] }, { duration: 0.22 });
        }
        const first = menu.querySelector("a, button");
        if (first) first.focus({ preventScroll: true });
      });
    });

    document.addEventListener("click", function () {
      roots.forEach(closeDropdown);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      roots.forEach(function (root) {
        const menu = root.querySelector("[data-dropdown-menu]");
        if (menu.hidden) return;
        closeDropdown(root);
        root.querySelector("[data-dropdown-trigger]").focus();
      });
    });
  }

  /* --- copy to clipboard -------------------------------------------------
     Buttons carry the value in data-copy and swap their own label. */

  function copyButtons() {
    document.addEventListener("click", async function (event) {
      const button = event.target.closest("[data-copy]");
      if (!button) return;

      try {
        await navigator.clipboard.writeText(button.dataset.copy);
      } catch (error) {
        // Clipboard permission can be refused; the value is always on screen.
        return;
      }

      button.dataset.copied = "true";
      if (window.Motion && !still()) {
        Motion.animate(button.querySelector(".copy-done") || button, { opacity: [0, 1] }, { duration: 0.2 });
      }
      clearTimeout(button._copyTimer);
      button._copyTimer = setTimeout(function () {
        button.dataset.copied = "false";
      }, 1600);
    });
  }

  /* --- progressive disclosure --------------------------------------------
     Height is animated from the measured content height, then released to
     auto so the panel keeps reflowing with its contents. */

  function disclosures() {
    document.querySelectorAll("[data-disclosure]").forEach(function (toggle) {
      const panel = document.getElementById(toggle.getAttribute("aria-controls"));
      if (!panel) return;

      toggle.addEventListener("click", function () {
        const opening = toggle.getAttribute("aria-expanded") !== "true";
        toggle.setAttribute("aria-expanded", String(opening));

        if (still() || !window.gsap) {
          panel.hidden = !opening;
          panel.style.height = "";
          return;
        }

        if (opening) {
          panel.hidden = false;
          gsap.fromTo(
            panel,
            { height: 0, opacity: 0 },
            {
              height: panel.scrollHeight,
              opacity: 1,
              duration: 0.38,
              ease: "power3.out",
              onComplete: function () { panel.style.height = "auto"; }
            }
          );
        } else {
          gsap.to(panel, {
            height: 0,
            opacity: 0,
            duration: 0.26,
            ease: "power2.in",
            onComplete: function () { panel.hidden = true; }
          });
        }
      });
    });
  }

  /* --- password reveal ---------------------------------------------------- */

  function passwordToggles() {
    document.querySelectorAll("[data-reveal]").forEach(function (button) {
      const input = document.getElementById(button.dataset.reveal);
      if (!input) return;

      button.addEventListener("click", function () {
        const shown = input.type === "text";
        input.type = shown ? "password" : "text";
        button.setAttribute("aria-label", shown ? "Show password" : "Hide password");
        button.querySelector("use").setAttribute("href", shown ? "#i-eye" : "#i-eye-off");
      });
    });
  }

  /* --- confirm dialogs ----------------------------------------------------
     A trigger names a <dialog>; confirming submits the trigger's own form. */

  function confirmDialogs() {
    document.querySelectorAll("[data-confirm]").forEach(function (form) {
      const dialog = document.getElementById(form.dataset.confirm);
      if (!dialog) return;

      form.addEventListener("submit", function (event) {
        if (form.dataset.confirmed === "true") return;
        event.preventDefault();

        dialog.returnValue = "";
        dialog.showModal();
        if (window.Motion && !still()) {
          Motion.animate(dialog, { opacity: [0, 1], scale: [0.97, 1] }, { duration: 0.26 });
        }

        dialog.addEventListener("close", function handler() {
          dialog.removeEventListener("close", handler);
          if (dialog.returnValue !== "confirm") return;
          form.dataset.confirmed = "true";
          form.requestSubmit();
        });
      });
    });
  }

  /* --- submit button loading ---------------------------------------------- */

  function submitStates() {
    document.querySelectorAll("form[data-loading-form]").forEach(function (form) {
      form.addEventListener("submit", function () {
        const button = form.querySelector('button[type="submit"]');
        if (button) button.dataset.loading = "true";
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    entrance();
    mobileNav();
    dropdowns();
    copyButtons();
    disclosures();
    passwordToggles();
    confirmDialogs();
    submitStates();
  });
})();
