// Light/dark theme toggle. The initial theme is applied in <head> (base.html) to
// avoid a flash; this only handles the button click and persists the choice.
(function () {
  var btn = document.getElementById("theme-toggle");
  if (!btn) return;

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function render() {
    // Show the icon for the theme you'd switch TO.
    btn.textContent = currentTheme() === "dark" ? "☀️" : "🌙";
  }

  btn.addEventListener("click", function () {
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("pse-theme", next);
    } catch (e) {}
    render();
  });

  render();
})();
