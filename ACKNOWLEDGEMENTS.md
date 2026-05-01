# Acknowledgements

WatchBrief V5's Bilibili subtitle provider implements its own provider boundary and BCC parser. It does not vendor or copy code from the projects below.

## Bilibili Subtitle Research References

- Bilibili Evolved — https://github.com/the1812/Bilibili-Evolved — MIT with additional redistribution notes in `LICENCE.md`. WatchBrief references its public Bilibili subtitle API flow: `aid/cid -> x/player/wbi/v2 -> subtitle_url -> BCC JSON`.
- Bilibili Obsidian Clipper — https://github.com/haixiong1997/Bilibili-Obsidian-Clipper — MIT. WatchBrief references its Bilibili metadata, subtitle track, BCC handling, and browser-session design ideas.
- BilibiliDown — https://github.com/nICEnnnnnnnLee/BilibiliDown — Apache-2.0. WatchBrief references its subtitle download and SRT conversion behavior.
- BBDown — https://github.com/nilaoda/BBDown — MIT. WatchBrief references its CLI behavior, subtitle-only verification path, and Bilibili subtitle fallback observations.
- yutto — https://github.com/yutto-dev/yutto — GPL-3.0-only. WatchBrief only used it as behavior context during research and does not copy or adapt its source code.

If future work copies or adapts substantial code from any third-party project, preserve the original copyright and license notices in this repository before distribution.
