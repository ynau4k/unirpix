(() => {
  "use strict";

  const stage = document.getElementById("slot-stage");
  const form = document.getElementById("form-slot");
  const reels = Array.from(document.querySelectorAll(".slot-reel"));
  if (!stage || !form || reels.length !== 3) return;

  const images = JSON.parse(stage.dataset.gatoImagens || "[]");
  const coinImage = stage.dataset.coinImage;
  const names = JSON.parse(stage.dataset.gatinhoNomes || "[]");
  const button = form.querySelector("button[type='submit']");
  const tokenInput = form.querySelector("[name='aposta_slot_token']");
  const balance = document.getElementById("slot-balance-value");
  const resultPanel = document.getElementById("slot-result-panel");
  const resultTitle = document.getElementById("slot-result-title");
  const resultCopy = document.getElementById("slot-result-copy");
  const soundToggle = document.getElementById("slot-sound-toggle");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let soundEnabled = true;
  let audioContext = null;
  let spinOscillator = null;
  let spinGain = null;
  let animationRunning = false;

  function makeCell(symbol) {
    const cell = document.createElement("span");
    cell.className = "slot-cat-cell";
    const artwork = document.createElement("img");
    artwork.className = "slot-cat-artwork";
    artwork.src = images[symbol];
    artwork.alt = "";
    artwork.draggable = false;
    cell.appendChild(artwork);
    cell.setAttribute("aria-label", names[symbol] || `Gato ${symbol}`);
    return cell;
  }

  function showStaticSymbols(symbols) {
    reels.forEach((reel, index) => {
      const strip = reel.querySelector(".slot-reel-strip");
      strip.replaceChildren(makeCell(symbols[index]));
      strip.style.transform = "translateY(0)";
    });
  }

  function ensureAudio() {
    if (!soundEnabled) return null;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return null;
    audioContext ||= new AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
    return audioContext;
  }

  function startSpinSound() {
    const context = ensureAudio();
    if (!context || spinOscillator) return;
    spinOscillator = context.createOscillator();
    spinGain = context.createGain();
    const wobble = context.createOscillator();
    const wobbleGain = context.createGain();
    spinOscillator.type = "triangle";
    spinOscillator.frequency.value = 118;
    spinGain.gain.value = 0.018;
    wobble.frequency.value = 5;
    wobbleGain.gain.value = 18;
    wobble.connect(wobbleGain).connect(spinOscillator.frequency);
    spinOscillator.connect(spinGain).connect(context.destination);
    spinOscillator.start();
    wobble.start();
    spinOscillator._wobble = wobble;
  }

  function stopSpinSound() {
    if (!spinOscillator || !audioContext) return;
    const oscillator = spinOscillator;
    const now = audioContext.currentTime;
    spinGain.gain.cancelScheduledValues(now);
    spinGain.gain.setTargetAtTime(0, now, 0.06);
    oscillator._wobble.stop(now + 0.3);
    oscillator.stop(now + 0.3);
    spinOscillator = null;
    spinGain = null;
  }

  function tone(frequency, delay = 0, duration = 0.13, volume = 0.04, wave = "sine") {
    const context = ensureAudio();
    if (!context) return;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const start = context.currentTime + delay;
    oscillator.type = wave;
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(volume, start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  function tickSound(index) {
    tone(440 + index * 115, 0, 0.075, 0.035, "triangle");
  }

  function playTripleSounds() {
    [523, 659, 784, 1047].forEach((frequency, index) => tone(frequency, index * 0.12, 0.28));
    for (let index = 0; index < 6; index += 1) {
      tone(980 + (index % 3) * 170, 0.52 + index * 0.105, 0.16, 0.027, "triangle");
    }
  }

  function playPairSound() {
    [587, 740, 880].forEach((frequency, index) => tone(frequency, index * 0.14, 0.2, 0.025));
    tone(1175, 0.48, 0.12, 0.018, "triangle");
  }

  function playLossSound() {
    tone(310, 0, 0.16, 0.025, "triangle");
    tone(230, 0.12, 0.22, 0.02, "triangle");
  }

  function formatCoins(cents) {
    return `${new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(cents / 100)} Cat's Coins`;
  }

  function animateBalance(from, to, duration) {
    if (!balance) return Promise.resolve();
    const start = performance.now();
    const badge = balance.closest(".slot-balance");
    badge?.classList.add("is-crediting");
    balance.classList.add("is-increasing");
    return new Promise((resolve) => {
      function frame(now) {
        const progress = Math.min(1, (now - start) / duration);
        const eased = 1 - (1 - progress) ** 3;
        const current = Math.round(from + (to - from) * eased);
        balance.textContent = formatCoins(current);
        balance.dataset.balanceCents = String(current);
        if (progress < 1) requestAnimationFrame(frame);
        else {
          balance.textContent = formatCoins(to);
          balance.dataset.balanceCents = String(to);
          balance.classList.remove("is-increasing");
          badge?.classList.remove("is-crediting");
          resolve();
        }
      }
      requestAnimationFrame(frame);
    });
  }

  function flyCoins(count) {
    if (reducedMotion || !coinImage || !balance || !stage) return Promise.resolve();
    const startRect = stage.getBoundingClientRect();
    const endRect = document.getElementById("slot-balance")?.getBoundingClientRect();
    if (!endRect) return Promise.resolve();
    const startX = startRect.left + startRect.width / 2;
    const startY = startRect.top + startRect.height / 2;
    const endX = endRect.left + endRect.width / 2;
    const endY = endRect.top + endRect.height / 2;
    const flights = [];
    for (let index = 0; index < count; index += 1) {
      const coin = document.createElement("img");
      coin.src = coinImage;
      coin.alt = "";
      coin.className = "slot-flying-coin";
      coin.setAttribute("aria-hidden", "true");
      coin.style.left = `${startX + (index - (count - 1) / 2) * 13}px`;
      coin.style.top = `${startY + ((index % 3) - 1) * 17}px`;
      document.body.appendChild(coin);
      flights.push(new Promise((resolve) => {
        const delay = index * 65;
        window.setTimeout(() => {
          coin.style.setProperty("--coin-x", `${endX - startX + ((index % 3) - 1) * 4}px`);
          coin.style.setProperty("--coin-y", `${endY - startY}px`);
          coin.classList.add("is-flying");
        }, delay);
        coin.addEventListener("transitionend", () => { coin.remove(); resolve(); }, { once: true });
        window.setTimeout(() => { coin.remove(); resolve(); }, delay + 1250);
      }));
    }
    return Promise.all(flights);
  }

  function setOutcomeHighlight(symbols, outcome) {
    stage.classList.remove("is-triple", "is-pair", "is-loss");
    reels.forEach((reel) => reel.classList.remove("is-winning"));
    if (outcome === "triple") {
      stage.classList.add("is-triple");
      reels.forEach((reel) => reel.classList.add("is-winning"));
    } else if (outcome === "pair") {
      stage.classList.add("is-pair");
      const repeated = symbols.find((symbol, index) => symbols.indexOf(symbol) !== index);
      reels.forEach((reel, index) => {
        if (symbols[index] === repeated) reel.classList.add("is-winning");
      });
    } else {
      stage.classList.add("is-loss");
    }
  }

  async function showResult(result) {
    const symbols = result.symbols;
    if (!Array.isArray(symbols) || symbols.length !== 3
      || symbols.some((symbol) => !Number.isInteger(symbol) || symbol < 0 || symbol > 10)
      || !["triple", "pair", "loss"].includes(result.outcome)) return;
    const distinctCount = new Set(symbols).size;
    if ((result.outcome === "triple" && distinctCount !== 1)
      || (result.outcome === "pair" && distinctCount !== 2)
      || (result.outcome === "loss" && distinctCount !== 3)) return;

    animationRunning = true;
    form.dataset.animating = "true";
    form.setAttribute("aria-busy", "true");
    if (button) {
      button.disabled = true;
      button.textContent = "Os rolos estão girando...";
    }
    stage.classList.remove("is-triple", "is-pair", "is-loss");
    document.querySelector(".slot-card")?.classList.remove("is-celebrating", "is-pair-celebrating");
    startSpinSound();

    const loops = reducedMotion ? 1 : 6;
    const durationBase = reducedMotion ? 0.28 : 3.65;
    const durations = reels.map((_, index) => durationBase + (reducedMotion ? index * 0.08 : index * 0.42));
    const finished = reels.map((reel, reelIndex) => new Promise((resolve) => {
      const strip = reel.querySelector(".slot-reel-strip");
      const height = reel.getBoundingClientRect().height;
      const stopIndex = loops * 11 + symbols[reelIndex];
      const cells = Array.from({ length: stopIndex + 1 }, (_, index) => makeCell(index % 11));
      strip.replaceChildren(...cells);
      strip.style.transition = "none";
      strip.style.transform = "translateY(0)";
      void strip.offsetHeight;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        strip.style.transition = `transform ${durations[reelIndex]}s cubic-bezier(.12,.72,.13,1)`;
        strip.style.transform = `translateY(-${stopIndex * height}px)`;
        window.setTimeout(resolve, (durations[reelIndex] + 0.35) * 1000);
      }));
    }));

    durations.forEach((duration, index) => window.setTimeout(() => tickSound(index), duration * 1000));
    await Promise.all(finished);
    stopSpinSound();
    setOutcomeHighlight(symbols, result.outcome);
    if (result.outcome === "triple") document.querySelector(".slot-card")?.classList.add("is-celebrating");
    if (result.outcome === "pair") document.querySelector(".slot-card")?.classList.add("is-pair-celebrating");
    if (resultPanel) resultPanel.hidden = false;
    if (resultTitle) resultTitle.textContent = result.title || "Resultado da rodada";

    const delta = Number(result.delta_cents || 0);
    const oldBalance = Number(result.old_balance_cents);
    const newBalance = Number(result.balance_cents);
    if (resultCopy) resultCopy.textContent = result.message || "Rodada concluída.";
    if (balance && Number.isInteger(newBalance)) {
      if (result.outcome === "triple" || result.outcome === "pair") {
        if (Number.isInteger(oldBalance) && newBalance >= oldBalance) {
          balance.textContent = formatCoins(oldBalance);
          balance.dataset.balanceCents = String(oldBalance);
          if (result.outcome === "triple") {
            playTripleSounds();
            const flight = flyCoins(8);
            await animateBalance(oldBalance, newBalance, reducedMotion ? 450 : 1900);
            await flight;
          } else {
            playPairSound();
            const flight = flyCoins(3);
            await animateBalance(oldBalance, newBalance, reducedMotion ? 350 : 1100);
            await flight;
          }
        } else {
          balance.textContent = formatCoins(newBalance);
          balance.dataset.balanceCents = String(newBalance);
        }
      } else {
        playLossSound();
        balance.textContent = formatCoins(newBalance);
        balance.dataset.balanceCents = String(newBalance);
      }
    } else if (delta < 0) {
      playLossSound();
    }
    // Confirmação final sempre usa os centavos devolvidos pelo servidor.
    if (balance && Number.isInteger(newBalance)) {
      balance.textContent = formatCoins(newBalance);
      balance.dataset.balanceCents = String(newBalance);
    }
    form.removeAttribute("aria-busy");
    delete form.dataset.animating;
    delete form.dataset.submitted;
    animationRunning = false;
    if (button) {
      button.disabled = false;
      button.innerHTML = 'Girar <span aria-hidden="true">✦</span>';
    }
  }

  showStaticSymbols([0, 4, 8]);

  async function submitSpin(event) {
    event.preventDefault();
    if (animationRunning || form.dataset.submitted === "true") return;
    form.dataset.submitted = "true";
    form.setAttribute("aria-busy", "true");
    if (resultPanel) resultPanel.hidden = true;
    if (button) {
      button.disabled = true;
      button.textContent = "Sorteando os gatos...";
    }
    startSpinSound();
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new FormData(form),
        headers: { Accept: "application/json" }, credentials: "same-origin",
      });
      const result = await response.json();
      if (result.next_token && tokenInput) tokenInput.value = result.next_token;
      if (!response.ok || !result.ok) {
        stopSpinSound();
        if (result.message && resultCopy) {
          if (resultPanel) resultPanel.hidden = false;
          if (resultTitle) resultTitle.textContent = "Não foi possível girar";
          resultCopy.textContent = result.message;
        }
        if (balance && Number.isInteger(result.balance_cents)) {
          balance.dataset.balanceCents = String(result.balance_cents);
          balance.textContent = formatCoins(result.balance_cents);
        }
        delete form.dataset.submitted;
        form.removeAttribute("aria-busy");
        if (button) {
          button.disabled = false;
          button.innerHTML = 'Girar <span aria-hidden="true">✦</span>';
        }
        return;
      }
      await showResult(result);
    } catch (_error) {
      stopSpinSound();
      if (resultPanel) resultPanel.hidden = false;
      if (resultTitle) resultTitle.textContent = "Conexão interrompida";
      if (resultCopy) resultCopy.textContent = "O saldo confirmado será consultado ao recarregar. Não envie a rodada novamente antes de conferir o histórico.";
      window.setTimeout(() => window.location.reload(), 1800);
    }
  }

  form.addEventListener("submit", submitSpin);
  if (soundToggle) {
    soundToggle.addEventListener("click", () => {
      soundEnabled = !soundEnabled;
      soundToggle.setAttribute("aria-pressed", String(soundEnabled));
      soundToggle.textContent = soundEnabled ? "Som: ligado" : "Som: desligado";
      if (!soundEnabled) stopSpinSound();
    });
  }

  if (stage.dataset.resultOutcome) {
    showResult({
      symbols: [1, 2, 3].map((index) => Number(stage.dataset[`symbol${index}`])),
      outcome: stage.dataset.resultOutcome,
      title: stage.dataset.resultTitle,
      message: stage.dataset.resultMessage,
      prize_cents: Number(stage.dataset.prizeCents),
      delta_cents: Number(stage.dataset.deltaCents),
      old_balance_cents: Number(stage.dataset.oldBalanceCents),
      balance_cents: Number(stage.dataset.newBalanceCents),
    });
  }
})();
