# artemis. landing site

Static public site for Artemis: plain HTML and one CSS file. No scripts, cookies, analytics or
third-party requests; every page sets a `default-src 'self'` Content Security Policy.

Preview locally:

```sh
cd site
python -m http.server 8765 --bind 127.0.0.1
```

Then open http://127.0.0.1:8765/.

Any static host works; all links are relative.

Fonts: Atkinson Hyperlegible Next and Mono (Braille Institute), self-hosted under the SIL Open
Font License 1.1 (`fonts/OFL-next.txt`, `fonts/OFL-mono.txt`).

Accessibility bar for any change: keyboard only works, focus is visible, one `h1` per page with no
skipped heading levels, text contrast of at least 7:1 on black, and the home page's one animation is
off under `prefers-reduced-motion`.
