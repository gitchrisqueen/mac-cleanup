# Recorded tool output

Captured from the maintainer's machine so parsers are tested against bytes the tools really
emit. Only the *invocation* is faked; the text is real. Re-capture when a tool version
changes and note it here.

| File | Command | Machine | Captured |
|---|---|---|---|
| `brew_cleanup_n.txt` | `brew cleanup -n` | MacBookPro15,1, macOS 15.8 (24H22), Homebrew at /usr/local | 2026-10-07 |
| `brew_cleanup_s_n.txt` | `brew cleanup -s -n` | same | 2026-10-07 |
| `simctl_runtime_list.json` | `xcrun simctl runtime list -j` | same, Xcode present | 2026-10-07 |
