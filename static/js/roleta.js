(() => {
  "use strict";

  const stage = document.getElementById("wheel-stage");
  const rotor = document.getElementById("wheel-rotor");
  const form = document.getElementById("form-aposta");
  if (!stage || !rotor || !form) return;

  const svgNS = "http://www.w3.org/2000/svg";
  const center = 210;
  const radius = 190;
  const labelRadius = 137;
  const step = 360 / 11;
  const colors = [
    "#694a86", "#8b69a8", "#543a70", "#a783c4", "#70508c",
    "#9672b2", "#5a4278", "#ad8bcb", "#62467e", "#84639f", "#4d375f",
  ];

  function point(angle, distance) {
    const radians = angle * Math.PI / 180;
    return {
      x: center + distance * Math.cos(radians),
      y: center + distance * Math.sin(radians),
    };
  }

  function makeSvgElement(name, attributes) {
    const element = document.createElementNS(svgNS, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  for (let number = 0; number <= 10; number += 1) {
    // Number zero is centered at the fixed pointer; indices then advance
    // clockwise in equal sectors. The server, never this script, picks result.
    const middle = -90 + number * step;
    const first = point(middle - step / 2, radius);
    const last = point(middle + step / 2, radius);
    const sector = makeSvgElement("path", {
      class: "wheel-sector",
      "data-number": number,
      fill: colors[number],
      d: `M ${center} ${center} L ${first.x.toFixed(3)} ${first.y.toFixed(3)} A ${radius} ${radius} 0 0 1 ${last.x.toFixed(3)} ${last.y.toFixed(3)} Z`,
    });
    rotor.appendChild(sector);

    const label = point(middle, labelRadius);
    const text = makeSvgElement("text", {
      class: "wheel-number",
      "data-number": number,
      x: label.x.toFixed(2),
      y: label.y.toFixed(2),
      "text-anchor": "middle",
      "dominant-baseline": "central",
    });
    text.textContent = String(number);
    rotor.appendChild(text);
  }

  const numberSelect = document.getElementById("numero");
  const pickedNumber = document.getElementById("wheel-selection-number");
  const submitButton = form.querySelector("button[type='submit']");

  function highlight(number) {
    rotor.querySelectorAll("[data-number]").forEach((element) => {
      element.classList.toggle("is-chosen", Number(element.dataset.number) === Number(number));
    });
    if (pickedNumber) {
      pickedNumber.textContent = Number.isInteger(Number(number)) && number !== ""
        ? String(number)
        : "Escolha um número";
    }
  }

  if (numberSelect) {
    numberSelect.addEventListener("change", () => highlight(numberSelect.value));
    if (numberSelect.value !== "") highlight(numberSelect.value);
  }

  const resultNumber = Number(stage.dataset.resultNumber);
  const chosenNumber = Number(stage.dataset.chosenNumber);
  const outcome = stage.dataset.resultOutcome;
  const hasServerResult = Number.isInteger(resultNumber) && resultNumber >= 0 && resultNumber <= 10
    && Number.isInteger(chosenNumber) && chosenNumber >= 0 && chosenNumber <= 10
    && (outcome === "win" || outcome === "loss");

  if (!hasServerResult) {
    document.querySelector("[data-spin-result]")?.removeAttribute("hidden");
    if (numberSelect && numberSelect.value !== "") highlight(numberSelect.value);
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitted === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitted = "true";
      form.setAttribute("aria-busy", "true");
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Processando aposta...";
      }
    });
    return;
  }

  if (numberSelect) numberSelect.value = String(chosenNumber);
  highlight(chosenNumber);
  form.setAttribute("aria-busy", "true");
  form.dataset.animating = "true";
  if (submitButton) {
    submitButton.disabled = true;
    submitButton.textContent = "A roleta está girando...";
  }

  const resultMessage = document.querySelector("[data-spin-result]");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const turns = reducedMotion ? 3 : 6;
  const finalOffset = ((360 - resultNumber * step) % 360 + 360) % 360;
  const finalRotation = turns * 360 + finalOffset;
  stage.dataset.finalRotation = finalRotation.toFixed(3);

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      rotor.style.transform = `rotate(${finalRotation}deg)`;
      stage.classList.add("is-spinning");
    });
  });

  let finished = false;
  function finishSpin() {
    if (finished) return;
    finished = true;
    stage.classList.remove("is-spinning");
    stage.classList.add(outcome === "win" ? "is-win" : "is-loss");
    if (resultMessage) resultMessage.hidden = false;
    if (outcome === "win") {
      document.querySelector(".roulette-card")?.classList.add("is-celebrating");
    }
    form.removeAttribute("aria-busy");
    delete form.dataset.animating;
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.innerHTML = 'Girar roleta <span aria-hidden="true">♡</span>';
    }
  }

  rotor.addEventListener("transitionend", (event) => {
    if (event.propertyName === "transform") finishSpin();
  }, { once: true });
  window.setTimeout(finishSpin, reducedMotion ? 1800 : 7200);
})();
