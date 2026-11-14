# artemis. landing site

Public site for Artemis: plain HTML, one stylesheet and one small script (`motion.js`, which runs
the home page marquee and the Pause motion button). No cookies, analytics or third-party requests.
Every page sets a `default-src 'self'` Content Security Policy, and `vercel.json` sends the same
policy plus `frame-ancestors 'none'` as headers.

Design ("Velocity"): black background, a slow purple gradient, liquid-glass panels, and the
`artemis.` wordmark in purple (`#B794FF`).

Preview locally:

```sh
cd site
python -m http.server 8765 --bind 127.0.0.1
```

Then open http://127.0.0.1:8765/.

Deploy on Vercel: import the repository and set **Root Directory** to `site`. No build step and no
framework preset ("Other").

Fonts: Atkinson Hyperlegible Next and Mono (Braille Institute), self-hosted under the SIL Open
Font License 1.1 (`fonts/OFL-next.txt`, `fonts/OFL-mono.txt`).

Team photos in `team/` are published with each person's consent, given on the team board. Add a
person's photo only after they agree.

Accessibility bar for any change: keyboard only works, focus is visible, one `h1` per page with no
skipped heading levels, text contrast of at least 4.5:1 everywhere (7:1 for body text on black),
moving content can be paused, and motion starts paused under `prefers-reduced-motion`. Pages work
without JavaScript; only the marquee and the pause button need it. No inline styles or scripts, so
the policy never needs `unsafe-inline`.
