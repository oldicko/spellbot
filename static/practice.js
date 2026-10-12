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
  const hearLabel = document.getElementById("hear-label");
  const nextButton = document.getElementById("next");
  const timerEl = document.getElementById("timer");
  const finish = document.getElementById("finish");
  const finishPanel = document.getElementById("finish-panel");
  const finishYes = document.getElementById("finish-yes");
  const finishNo = document.getElementById("finish-no");
  const keys = document.getElementById("letter-keys");

  const audio = new Audio();
  const touchQuery = window.matchMedia("(hover: none) and (pointer: coarse)");
  let useKeys = false;
  let caretPos = 0;
  let caretEnd = 0;
  let pinning = false;
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
    keys.querySelectorAll("button").forEach(function (button) {
      button.disabled = locked;
    });
  }

  function syncKeys() {
    keys.hidden = !useKeys || form.hidden;
  }

  function rememberCaret() {
    if (document.activeElement !== input || input.selectionStart == null) {
      return;
    }
    caretPos = input.selectionStart;
    caretEnd = input.selectionEnd;
  }

  function caret() {
    if (document.activeElement === input && input.selectionStart != null) {
      return [input.selectionStart, input.selectionEnd];
    }
    return [caretPos, caretEnd];
  }

  function writeAt(next, pos) {
    input.value = next;
    caretPos = pos;
    caretEnd = pos;
    if (document.activeElement === input && input.setSelectionRange) {
      input.setSelectionRange(pos, pos);
    }
  }

  function insert(ch) {
    if (input.disabled || phase === "reveal") {
      return;
    }
    const bounds = caret();
    const next = input.value.slice(0, bounds[0]) + ch + input.value.slice(bounds[1]);
    if (next.length > 80) {
      return;
    }
    writeAt(next, bounds[0] + ch.length);
  }

  function backspace() {
    if (input.disabled || phase === "reveal") {
      return;
    }
    const bounds = caret();
    let start = bounds[0];
    const end = bounds[1];
    if (start === end) {
      if (start === 0) {
        return;
      }
      start -= 1;
    }
    writeAt(input.value.slice(0, start) + input.value.slice(end), start);
  }

  function applyTouchMode() {
    useKeys = touchQuery.matches;
    document.body.classList.toggle("touch-keys", useKeys);
    // Readonly stops the tablet keyboard, so its spelling suggestions stay off.
    input.readOnly = useKeys;
    input.inputMode = useKeys ? "none" : "text";
    input.spellcheck = false;
    input.autocapitalize = "none";
    if ("autocorrect" in input) {
      input.autocorrect = false;
    }
    if ("writingSuggestions" in input) {
      input.writingSuggestions = "false";
    }
    syncKeys();
  }

  function pinViewport() {
    if (pinning) {
      return;
    }
    pinning = true;
    const vv = window.visualViewport;
    const height = vv ? vv.height : window.innerHeight;
    const top = vv ? vv.offsetTop : 0;
    document.documentElement.style.setProperty("--vv-height", Math.round(height) + "px");
    document.documentElement.style.setProperty("--vv-top", Math.round(top) + "px");
    document.body.classList.toggle("short-practice", height < 700);
    if (window.scrollX || window.scrollY) {
      window.scrollTo(0, 0);
    }
    pinning = false;
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
      hearLabel.textContent = "Hear the word";
    }).catch(function () {
      hear.classList.add("needs-tap");
      hearLabel.textContent = "Tap to hear the word";
    });
  }

  function focusField() {
    if (form.hidden || input.disabled || !finishPanel.hidden) {
      return;
    }
    if (document.activeElement === input) {
      return;
    }
    input.focus();
    if (input.setSelectionRange) {
      input.setSelectionRange(caretPos, caretEnd);
    }
  }

  function showSpell(parts, url) {
    phase = "spell";
    window.clearTimeout(advanceTimer);
    feedbackEl.textContent = "";
    hintEl.replaceChildren();
    showAnswerWord("", false);
    renderParts(parts);
    input.value = "";
    caretPos = 0;
    caretEnd = 0;
    nextButton.hidden = true;
    form.hidden = false;
    setBusy(false);
    syncKeys();
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
        caretPos = input.value.length;
        caretEnd = caretPos;
      }
      nextButton.hidden = true;
      form.hidden = false;
      setBusy(false);
      syncKeys();
      input.focus();
      if (input.setSelectionRange) {
        input.setSelectionRange(caretPos, caretEnd);
      }
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
      syncKeys();
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

  let pointedAt = 0;

  function pressKey(button) {
    const key = button.getAttribute("data-key");
    if (key === "backspace") {
      backspace();
      return;
    }
    if (key) {
      insert(key);
    }
  }

  function releaseKey() {
    keys.querySelectorAll(".pressed").forEach(function (button) {
      button.classList.remove("pressed");
    });
  }

  keys.addEventListener("pointerdown", function (event) {
    const button = event.target.closest("button");
    if (!button || button.disabled) {
      return;
    }
    event.preventDefault();
    pointedAt = Date.now();
    button.classList.add("pressed");
    pressKey(button);
  });
  keys.addEventListener("pointerup", releaseKey);
  keys.addEventListener("pointercancel", releaseKey);
  keys.addEventListener("click", function (event) {
    const button = event.target.closest("button");
    if (!button || button.disabled || Date.now() - pointedAt < 700) {
      return;
    }
    pressKey(button);
  });
  document.addEventListener("selectionchange", rememberCaret);
  input.addEventListener("beforeinput", function (event) {
    if (!useKeys && event.inputType === "insertText" && event.data === " ") {
      event.preventDefault();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    const target = event.target;
    if (target && target.closest && target.closest("#finish, #finish-panel")) {
      return;
    }
    if (!finishPanel.hidden) {
      return;
    }
    if (!useKeys) {
      if (form.hidden || input.disabled || phase === "reveal") {
        return;
      }
      if (event.key === " " && !event.repeat) {
        event.preventDefault();
        speak(audioUrl);
        focusField();
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        form.requestSubmit();
      }
      return;
    }
    if (input.disabled || phase === "reveal") {
      return;
    }
    if (event.key === "Backspace") {
      event.preventDefault();
      backspace();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      form.requestSubmit();
      return;
    }
    if (event.key.length === 1) {
      event.preventDefault();
      insert(event.key);
    }
  });
  document.addEventListener("pointerdown", function (event) {
    // A mouse click on the sentence would blur the field. Cancelling that
    // press keeps the caret in place. Touch presses still scroll.
    if (event.pointerType === "touch") {
      return;
    }
    if (form.hidden || input.disabled || !finishPanel.hidden) {
      return;
    }
    const target = event.target;
    if (target && target.closest && target.closest("#spelling, #finish, #finish-panel")) {
      return;
    }
    event.preventDefault();
  });
  document.addEventListener("focusin", function (event) {
    if (event.target === input) {
      return;
    }
    if (event.target.closest && event.target.closest("#finish, #finish-panel")) {
      return;
    }
    window.setTimeout(focusField, 0);
  });

  function holdFocus(event) {
    if (event.target.closest && event.target.closest("button")) {
      event.preventDefault();
    }
  }
  hear.addEventListener("pointerdown", holdFocus);
  check.addEventListener("pointerdown", holdFocus);
  hear.addEventListener("click", function () {
    speak(audioUrl);
    focusField();
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
    caretPos = input.value.length;
    caretEnd = caretPos;
  }
  applyTouchMode();
  pinViewport();
  if (touchQuery.addEventListener) {
    touchQuery.addEventListener("change", applyTouchMode);
  }
  window.addEventListener("resize", pinViewport);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", pinViewport);
    window.visualViewport.addEventListener("scroll", pinViewport);
  }
  input.focus();
  if (input.setSelectionRange) {
    input.setSelectionRange(caretPos, caretEnd);
  }
  speak(boot.audioUrl);
  paintTimer();
  window.setInterval(paintTimer, 250);
})();
