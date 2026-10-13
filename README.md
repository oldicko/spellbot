# spellbot

A small spelling practice site for a home server. A child picks their name, hears a word, and types it. You keep the word lists, the sitting length, and the scores.

The voice is British English. Without a cloud key it stays offline, using Piper with the clearer Cori high model, spoken slowly so each sound in the word stays distinct. With a Gemini key, each spelling word is recorded once and kept next to the scores. The container does not need the internet after those recordings exist. Scores live in a SQLite file on a Docker volume.

## Run it on Proxmox

Use a machine or LXC that already has Docker. From this directory:

```bash
cp .env.example .env
```

Edit `.env` and set `ADMIN_PASSWORD` to something the children will not guess. Then:

```bash
docker compose up -d --build
```

Open `http://<the-server>:8080`. Grown-up pages are at `http://<the-server>:8080/admin`.

The first build downloads the offline voice model, so it needs the network once. Later restarts do not. The first word after a start can take a few seconds while the voice wakes up. After that, repeats are quick.

To record a clearer voice, create a key at [Google AI Studio](https://aistudio.google.com/apikey) and set `GEMINI_API_KEY` in `.env`. Restart spellbot. It saves a slow British reading of every word on every list onto the data volume. The voice service allows 10 recordings a minute, so a full list takes a while, and spellbot waits when it reaches that limit. Practice plays a saved recording as soon as that word is ready, and uses the offline voice until then. Open Play the voice on the Children page to hear it. `GEMINI_TTS_VOICE` chooses the speaker (the default is `Sulafat`). Changing the voice records the words again. The key stays in `.env` and is not sent to the tablets.

Keep port 8080 on the home network. This app is not meant to be published on the internet. If you put HTTPS in front of it, set `SPELLBOT_SECURE_COOKIES=1` in `.env`.

The database is the `spellbot-data` volume. Copy that volume's `spellbot.sqlite` file if you want a backup.

## A sitting

1. Add a child and the minutes for one sitting (1 to 60).
2. Create a word list and paste words. Assign the list to one or both children.
3. The child opens the site, taps their name, then Start.
4. The sentence stays on screen with the word blanked. The voice says the word on its own. Hear the word plays it again.
5. One wrong try shows green for a letter in the right place and orange for a letter that is not. They get one chance to fix it. A correct first try scores 2 points. A correct second look scores 1. A miss scores 0.
6. Finish, or wait for the timer. The points and a chart of recent sittings are saved. While they spell, a bar shows their points against the pace of their best sitting.

Capital letters do not matter. Apostrophes do, so `they're` and `theyre` are different. A curly apostrophe from a tablet keyboard still counts.

Start mixes every list assigned to that child. Words they miss, and words not learned yet, come up more often than words they already know.

A word is learned after three correct first tries in a row. One later miss puts it back to practising.

## Word lists

```
they're | They're going to the park.
their | Their coats are on the pegs.
there | The book is over there.
```

`examples/homophones.txt` is a short list in that shape. Paste it on the list page. A repeated word updates its sentence.

## Grown-up statistics

Each child has an average score in points across finished sittings, a trend from recent sittings, and a chart. The word list shows learned, practising, and not started. A wrong first try is kept so you can see what they wrote.

## Voice licence

spellbot itself is MIT. The image also installs [Piper](https://github.com/OHF-Voice/piper1-gpl) (`piper-tts`, GPL-3.0-or-later) and the Cori high voice. Cori is a UK English voice trained on public-domain LibriVox recordings.

## Develop without Docker

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
$env:ADMIN_PASSWORD = "a-local-password"
.venv\Scripts\python -m app
```

Without the voice file, the pages still work and the Hear button reports that the voice is not available. Tests do not need the voice model:

```bash
.venv\Scripts\python -m pytest
```
