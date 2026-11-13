// Applies the saved theme before the page paints (loaded in <head>), and wires the
// theme button in the nav. "system" follows the device setting; "light"/"dark" override it.
(function () {
  "use strict";
  const KEY = "theme", ORDER = ["system", "light", "dark"], LABEL = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" };
  const read = () => { try { return localStorage.getItem(KEY); } catch (_) { return null; } };
  const apply = (t) => {
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
  };
  let theme = ORDER.includes(read()) ? read() : "system";
  apply(theme);

  document.addEventListener("DOMContentLoaded", () => {
    const nav = document.querySelector("nav.top");
    if (!nav) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme";
    button.title = "Switch between system, light and dark theme";
    const draw = () => { button.textContent = LABEL[theme]; };
    button.addEventListener("click", () => {
      theme = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
      apply(theme);
      try { theme === "system" ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, theme); } catch (_) { /* not saved */ }
      draw();
    });
    draw();
    nav.append(button);
  });
})();
