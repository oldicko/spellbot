(function () {
  const boot = JSON.parse(document.getElementById("boot").textContent);
  const sentenceEl = document.getElementById("sentence");
  const feedbackEl = document.getElementById("feedback");
  const hintEl = document.getElementById("hint");
  const answerWord = document.getElementById("answer-word");
  const form = document.getElementById("answer-form");
  const input = document.getElementById("spelling");
  const check = document.getElementById("check");
  const hear = document.getElementById("hear");
  const nextButton = document.getElementById("next");
  const timerEl = document.getElementById("timer");
  const finish = document.getElementById("finish");
  const finishPanel = document.getElementById("finish-panel");
  const finishYes = document.getElementById("finish-yes");
  const finishNo = document.getElementById("finish-no");

  const audio = new Audio();
  let deadline = Date.now() + boot.remainingSeconds * 1000;
  let phase = boot.phase || "spell";
  let audioUrl = boot.audioUrl;
  let busy = false;
  let advanceTimer = null;
  let pending = null;

  function renderParts(parts) {
    sentenceEl.replaceChildren();
    (parts || []).forEach(function (part) {
      if (part.kind === "blank") {
        const blank = document.createElement("span");
        blank.className = "blank";
        const hidden = document.createElement("span");
        hidden.className = "sr-only";
        hidden.textContent = "blank";
        blank.appendChild(hidden);
        sentenceEl.appendChild(blank);
        return;
      }
      if (part.kind === "word") {
        const word = document.createElement("span");
        word.className = "filled-word " + (part.tone || "show");
        word.textContent = part.text || "";
        sentenceEl.appendChild(word);
        return;
      }
      sentenceEl.appendChild(document.createTextNode(part.text || ""));
    });
  }

  function renderHint(hint) {
    hintEl.replaceChildren();
    if (!hint || !hint.letters || !hint.letters.length) {
      return;
    }
    const row = document.createElement("p");
    row.className = "letters";
    hint.letters.forEach(function (letter) {
      const span = document.createElement("span");
      span.className = "letter " + (letter.ok ? "ok" : "no");
      span.textContent = letter.ch === " " ? "·" : letter.ch;
      row.appendChild(span);
    });
    hintEl.appendChild(row);
    if (hint.note) {
      const note = document.createElement("p");
      note.className = "length-note";
      note.textContent = hint.note;
      hintEl.appendChild(note);
    }
  }

  function showAnswerWord(text, visible) {
    if (!visible || !text) {
      answerWord.hidden = true;
      answerWord.textContent = "";
      return;
    }
    answerWord.hidden = false;
    answerWord.textContent = text;
  }

  function setBusy(value) {
    busy = value;
    const locked = value || phase === "reveal";
    check.disabled = locked;
    input.disabled = locked;
  }

  function speak(url) {
    if (!url) {
      return;
    }
    audioUrl = url;
    audio.src = url;
    const played = audio.play();
    if (!played || !played.then) {
      return;
    }
    played.then(function () {
      hear.classList.remove("needs-tap");
      hear.textContent = "Hear the word";
    }).catch(function () {
      hear.classList.add("needs-tap");
      hear.textContent = "Tap to hear the word";
    });
  }

  function showSpell(parts, url) {
    phase = "spell";
    window.clearTimeout(advanceTimer);
    feedbackEl.textContent = "";
    hintEl.replaceChildren();
    showAnswerWord("", false);
    renderParts(parts);
    input.value = "";
    nextButton.hidden = true;
    form.hidden = false;
    setBusy(false);
    input.focus();
    speak(url);
  }

  function goOn() {
    window.clearTimeout(advanceTimer);
    const data = pending;
    pending = null;
    if (!data || data.phase === "done" || !data.next) {
      window.location.assign((data && data.resultsUrl) || boot.resultsUrl);
      return;
    }
    showSpell(data.next.parts, data.next.audioUrl);
  }

  function schedule(data) {
    pending = data;
    nextButton.hidden = false;
    nextButton.textContent = data.phase === "done" ? "See your score" : "Next";
    const wait = typeof data.pauseMs === "number" ? data.pauseMs : 1200;
    if (wait <= 0) {
      goOn();
      return;
    }
    advanceTimer = window.setTimeout(goOn, wait);
  }

  function apply(data) {
    if (data.error === "empty") {
      feedbackEl.textContent = "Type the word first.";
      setBusy(false);
      input.focus();
      return;
    }
    if (data.error) {
      feedbackEl.textContent = "This sitting is not available.";
      return;
    }
    if (typeof data.remainingSeconds === "number") {
      deadline = Date.now() + data.remainingSeconds * 1000;
      paintTimer();
    }
    if (data.phase === "retry") {
      phase = "retry";
      feedbackEl.textContent = data.message || "Have another go.";
      renderHint(data.hint);
      showAnswerWord("", false);
      if (typeof data.prefill === "string") {
        input.value = data.prefill;
      }
      nextButton.hidden = true;
      form.hidden = false;
      setBusy(false);
      input.focus();
      return;
    }
    if (data.phase === "next" || data.phase === "done") {
      phase = "reveal";
      feedbackEl.textContent = data.message || "";
      hintEl.replaceChildren();
      if (data.reveal) {
        renderParts(data.reveal.parts);
        showAnswerWord(data.reveal.word, Boolean(data.reveal.showWord));
      }
      form.hidden = true;
      setBusy(true);
      schedule(data);
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy || phase === "reveal") {
      return;
    }
    if (!input.value.trim()) {
      feedbackEl.textContent = "Type the word first.";
      input.focus();
      return;
    }
    setBusy(true);
    fetch(boot.answerUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ spelling: input.value }),
    }).then(function (response) {
      return response.json();
    }).then(apply).catch(function () {
      feedbackEl.textContent = "Try that again. The page could not reach spellbot.";
      setBusy(false);
    });
  });

  hear.addEventListener("click", function () {
    speak(audioUrl);
  });
  nextButton.addEventListener("click", goOn);
  finish.addEventListener("click", function () {
    finishPanel.hidden = false;
  });
  finishNo.addEventListener("click", function () {
    finishPanel.hidden = true;
    input.focus();
  });
  finishYes.addEventListener("click", endSitting);

  function endSitting() {
    if (busy && phase !== "reveal") {
      return;
    }
    setBusy(true);
    fetch(boot.finishUrl, { method: "POST", headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        window.location.assign(data.resultsUrl || boot.resultsUrl);
      })
      .catch(function () {
        window.location.assign(boot.resultsUrl);
      });
  }

  function paintTimer() {
    const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    const minutes = Math.floor(left / 60);
    const seconds = left % 60;
    timerEl.textContent = minutes + ":" + String(seconds).padStart(2, "0");
    timerEl.classList.toggle("low", left <= 30 && left > 0);
    if (left <= 0 && !busy && phase === "spell") {
      endSitting();
    }
  }

  renderParts(boot.parts);
  if (boot.phase === "retry") {
    phase = "retry";
    feedbackEl.textContent = "Have another go.";
    renderHint(boot.hint);
    input.value = boot.prefill || "";
  }
  input.focus();
  speak(boot.audioUrl);
  paintTimer();
  window.setInterval(paintTimer, 250);
})();
