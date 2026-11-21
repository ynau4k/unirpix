(() => {
  "use strict";

  const stage = document.getElementById("slot-stage");
  const form = document.getElementById("form-slot");
  const reels = Array.from(document.querySelectorAll(".slot-reel"));
  if (!stage || !form || reels.length !== 3) return;

  const sprite = stage.dataset.catSprite;
  const button = form.querySelector("button[type='submit']");
  const resultNotice = document.querySelector("[data-slot-result]");
  const iconsCount = 11;
  const initialSymbols = [0, 4, 8];

  function makeCell(symbol) {
    const cell = document.createElement("span");
    cell.className = "slot-cat-cell";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `${sprite}#gato-${symbol}`);
    svg.appendChild(use);
    cell.appendChild(svg);
    return cell;
  }

  function showStaticSymbols(symbols) {
    reels.forEach((reel, index) => {
      const strip = reel.querySelector(".slot-reel-strip");
      strip.replaceChildren(makeCell(symbols[index]));
      strip.style.transform = "translateY(0)";
    });
  }

  showStaticSymbols(initialSymbols);

  form.addEventListener("submit", (event) => {
    if (form.dataset.animating === "true" || form.dataset.submitted === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitted = "true";
    form.setAttribute("aria-busy", "true");
    if (button) {
      button.disabled = true;
      button.textContent = "Preparando os rolos...";
    }
  });

  const symbols = [1, 2, 3].map((index) => Number(stage.getAttribute(`data-symbol-${index}`)));
  const outcome = stage.dataset.resultOutcome;
  const validSymbols = symbols.every((symbol) => Number.isInteger(symbol) && symbol >= 0 && symbol < iconsCount);
  const tripleMatch = validSymbols && symbols[0] === symbols[1] && symbols[1] === symbols[2];
  const hasServerResult = validSymbols && (outcome === "win" || outcome === "loss")
    && ((outcome === "win") === tripleMatch);

  if (!hasServerResult) {
    if (stage.dataset.resultOutcome) resultNotice?.removeAttribute("hidden");
    return;
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const loops = reducedMotion ? 1 : 6;
  const longestDuration = reducedMotion ? 1.2 : 5.5;
  let finishedReels = 0;
  let finished = false;
  form.dataset.animating = "true";
  if (button) {
    button.disabled = true;
    button.textContent = "Os gatinhos estão girando...";
  }

  reels.forEach((reel, reelIndex) => {
    const strip = reel.querySelector(".slot-reel-strip");
    const cellHeight = reel.getBoundingClientRect().height;
    const stopIndex = loops * iconsCount + symbols[reelIndex];
    const cells = Array.from({ length: stopIndex + 1 }, (_, index) => makeCell(index % iconsCount));
    strip.replaceChildren(...cells);
    strip.style.transition = "none";
    strip.style.transform = "translateY(0)";
    // Force layout so the browser starts the transition from the first symbol.
    void strip.offsetHeight;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const duration = reducedMotion ? 0.8 + reelIndex * 0.1 : 4.6 + reelIndex * 0.45;
        strip.style.transition = `transform ${duration}s cubic-bezier(.12,.72,.13,1)`;
        strip.style.transform = `translateY(-${stopIndex * cellHeight}px)`;
      });
    });
    strip.addEventListener("transitionend", (event) => {
      if (event.propertyName !== "transform") return;
      finishedReels += 1;
      if (finishedReels === reels.length) finish();
    }, { once: true });
  });

  function finish() {
    if (finished) return;
    finished = true;
    stage.classList.add(outcome === "win" ? "is-win" : "is-loss");
    if (resultNotice) resultNotice.hidden = false;
    if (outcome === "win") document.querySelector(".slot-card")?.classList.add("is-celebrating");
    form.removeAttribute("aria-busy");
    delete form.dataset.animating;
    if (button) {
      button.disabled = false;
      button.innerHTML = 'Girar os gatinhos <span aria-hidden="true">♡</span>';
    }
  }

  window.setTimeout(finish, (longestDuration + 0.5) * 1000);
})();
