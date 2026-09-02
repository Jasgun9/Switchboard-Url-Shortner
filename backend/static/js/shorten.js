/* Homepage shorten form.

   No JS: the form posts to the Django view and the server renders the result.
   With JS: same thing through the REST API, but the form turns into the result
   in place. That swap is the interaction worth having, so it's worth the extra
   code path. */

(function () {
  "use strict";

  const form = document.querySelector("[data-shorten]");
  if (!form) return;

  const formBlock = document.querySelector("[data-shorten-form]");
  const result = document.querySelector("[data-result]");
  const submit = form.querySelector('button[type="submit"]');
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  const el = {
    url: result.querySelector("[data-result-url]"),
    copy: result.querySelector("[data-result-copy]"),
    dest: result.querySelector("[data-result-dest]"),
    qr: result.querySelector("[data-result-qr]"),
    download: result.querySelector("[data-result-download]"),
    analytics: result.querySelector("[data-result-analytics]"),
    reset: result.querySelector("[data-result-reset]")
  };

  function truncate(value, limit) {
    return value.length > limit ? value.slice(0, limit) + "…" : value;
  }

  function fieldValue(name) {
    const input = form.querySelector('[name="' + name + '"]');
    return input && input.value.trim() ? input.value.trim() : null;
  }

  function showError(field, message) {
    const holder = form.querySelector('[data-error-for="' + field + '"]')
      || form.querySelector('[data-error-for="__all__"]');
    if (!holder) return;
    holder.querySelector("span").textContent = message;
    holder.hidden = false;
    if (field === "destination") form.querySelector(".shorten-row").classList.add("invalid");
  }

  function clearErrors() {
    form.querySelectorAll("[data-error-for]").forEach(function (holder) { holder.hidden = true; });
    form.querySelectorAll(".field.invalid").forEach(function (f) { f.classList.remove("invalid"); });
    form.querySelector(".shorten-row").classList.remove("invalid");
  }

  function fillResult(link) {
    el.url.textContent = link.short_url;
    el.url.href = link.short_url;
    el.copy.dataset.copy = link.short_url;
    el.copy.dataset.copied = "false";
    // Same truncation the server does. Full URL goes on the title attribute.
    el.dest.textContent = "Redirects to " + truncate(link.destination, 90);
    el.dest.title = link.destination;
    el.qr.src = link.qr_url;
    el.download.href = link.qr_url;
    el.download.setAttribute("download", link.code + ".png");
    if (el.analytics) el.analytics.href = "/links/" + link.id + "/analytics";
  }

  /* Form collapses, result rises into the gap. One timeline, so the two halves
     can't drift apart. */
  function revealResult() {
    if (reduced.matches || !window.gsap) {
      formBlock.style.display = "none";
      result.classList.add("is-visible");
      el.copy.focus({ preventScroll: true });
      return;
    }

    // Result only takes up space once the form is gone, or the page jumps
    // while the two cross over.
    gsap.timeline({ defaults: { ease: "power2.out" } })
      .to(formBlock, { opacity: 0, y: -6, duration: 0.16 })
      .set(formBlock, { display: "none" })
      .add(function () { result.classList.add("is-visible"); })
      .fromTo(result, { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: 0.3 })
      .fromTo(
        result.querySelectorAll(".result-url, [data-result-dest], .row"),
        { opacity: 0, y: 6 },
        { opacity: 1, y: 0, duration: 0.24, stagger: 0.05, clearProps: "transform" },
        "-=0.18"
      )
      .add(function () { el.copy.focus({ preventScroll: true }); });
  }

  function restoreForm() {
    const input = form.querySelector('[name="destination"]');

    if (reduced.matches || !window.gsap) {
      result.classList.remove("is-visible");
      formBlock.style.display = "";
      input.focus();
      return;
    }

    gsap.timeline({ defaults: { ease: "power2.out" } })
      .to(result, { opacity: 0, y: 6, duration: 0.15 })
      .set(result, { clearProps: "all" })
      .add(function () {
        result.classList.remove("is-visible");
        formBlock.style.display = "";
      })
      .fromTo(formBlock, { opacity: 0, y: -6 }, { opacity: 1, y: 0, duration: 0.24, clearProps: "transform,opacity" })
      .add(function () { input.focus(); });
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    clearErrors();

    const destination = fieldValue("destination");
    if (!destination) {
      showError("destination", "Enter a URL to shorten.");
      return;
    }

    const payload = { destination: destination };
    const alias = fieldValue("alias");
    const expires = fieldValue("expires_at");
    const password = fieldValue("password");
    if (alias) payload.alias = alias;
    if (expires) payload.expires_at = expires;
    if (password) payload.password = password;

    submit.dataset.loading = "true";

    try {
      const response = await fetch("/api/v1/urls/", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify(payload)
      });
      const body = await response.json();

      if (!response.ok) {
        const error = body.error || {};
        const details = error.details || {};
        const field = Object.keys(details)[0];
        if (field) showError(field, details[field][0]);
        else showError("__all__", error.message || "Something went wrong. Please try again.");
        return;
      }

      form.reset();
      fillResult(body);
      revealResult();
    } catch (networkError) {
      // The plain form post still works, so say so rather than failing silently.
      showError("__all__", "Could not reach the server. Check your connection and try again.");
    } finally {
      submit.dataset.loading = "false";
    }
  });

  el.reset.addEventListener("click", restoreForm);
})();
