# spellbot

A small spelling practice site for a home server. A child picks their name, hears a word, and types it. You keep the word lists, the sitting length, and the scores.

The voice is offline British English. It uses Piper with the clearer Cori high model, spoken slowly so each sound in the word stays distinct. The container does not need the internet after it is built. Scores live in a SQLite file on a Docker volume.

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

The first build downloads the voice model, so it needs the network once. Later restarts do not. The first word after a start can take a few seconds while the voice wakes up. After that, repeats are quick.

Keep port 8080 on the home network. This app is not meant to be published on the internet. If you put HTTPS in front of it, set `SPELLBOT_SECURE_COOKIES=1` in `.env`.

The database is the `spellbot-data` volume. Copy that volume's `spellbot.sqlite` file if you want a backup.

## A sitting

1. Add a child and the minutes for one sitting (1 to 60).
2. Create a word list and paste words. Assign the list to one or both children.
3. The child opens the site, taps their name, then Start.
4. The sentence stays on screen with the word blanked. The voice says the word on its own. Hear the word plays it again.
5. One wrong try shows green for a letter in the right place and orange for a letter that is not. They get one chance to fix it. Only the first try counts.
6. Finish, or wait for the timer. The score and a chart of recent sittings are saved.

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

Each child has an average score across every first try, a trend from recent sittings, and a chart. The word list shows learned, practising, and not started. A wrong first try is kept so you can see what they wrote.

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
