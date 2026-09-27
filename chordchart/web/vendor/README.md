# Vendored JavaScript

Served by `chordchart serve` and the desktop app at `/vendor/<file>`, so the page works
offline (no CDN). Only the notes feature uses it, so the lite desktop build leaves it out.

## opensheetmusicdisplay.min.js

OpenSheetMusicDisplay 2.1.3 renders the notes' MusicXML as sheet music (the Sheet view).

- Source: https://registry.npmjs.org/opensheetmusicdisplay/-/opensheetmusicdisplay-2.1.3.tgz,
  file `package/build/opensheetmusicdisplay.min.js`. The tarball matched npm's published
  integrity `sha512-DwHaGM0GgXanmmrSi++0eYWORAH+77yiO+nwFuf/66lq4UYrFPcyrwZuLXwQQQ+kUCnsbnON3rEsurgKdWesCA==`.
- SHA-256 of the file: `099b2125aef055ca4faae75957037404973f9451544b52d9b3a0b1f788b33581`
- License: BSD-3-Clause (`opensheetmusicdisplay-LICENSE.txt`). The bundle also contains
  VexFlow 1.2.93 (MIT), JSZip 3.10.1 (MIT or GPL-3.0-or-later; used under MIT),
  loglevel (MIT) and typescript-collections (MIT); their license texts are in
  `opensheetmusicdisplay-NOTICE.txt`.
- It's self-contained: no fonts, workers or other files are loaded.

To upgrade: download the new tarball, check its `dist.integrity` from
`https://registry.npmjs.org/opensheetmusicdisplay/<version>`, copy the two files, and
update the version and hashes here and in `packaging/collect_licenses.py`.
