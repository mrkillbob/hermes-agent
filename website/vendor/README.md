# Local SVGR dependency cleanup

This private local artifact uses the published `@svgr/plugin-svgo`8.1.0 executable, types,
source map, README and MIT license bytes. Its package metadata removes the unused `deepmerge`
dependency, omits the original publication's `gitHead`, and sets `private: true`. The8.1.0
version identifies its upstream base; the `file:` URL and SHA-512 integrity identify this
local rebuild. It is not a new upstream release or a claim that deepmerge4.3.1 is fixed.

`svgr-plugin-svgo-provenance.json` records the original archive integrity/source commit,
every rebuilt file hash and local archive integrity. To reproduce the exact artifact,
obtain the original public archive at
`https://registry.npmjs.org/@svgr/plugin-svgo/-/plugin-svgo-8.1.0.tgz` and run:

```sh
python rebuild-svgr-plugin-svgo.py /path/to/plugin-svgo-8.1.0.tgz
```

The script performs no network request or package execution and refuses different input
or output bytes. The included LICENSE is inside the verified archive.

The direct development dependency gives npm a root-relative artifact identity; the `$`
override applies that same identity to the transitive SVGR consumer. A plain relative
override was rejected because npm resolved it beneath the webpack consumer. The root
reference successfully regenerated the lock offline with scripts/audit disabled and the
existing14-day minimum age. All non-SVGR package nodes remain unchanged; deepmerge is
actually absent from the resolved graph. No scanner filtering or age exemption was added.

This candidate still needs normal website integration/build qualification and review
before adoption. Prefer replacing it with a genuine upstream cleanup release once
available, regenerating the lock and verifying that deepmerge stays absent. Do not
silently refresh the archive or relax its exact hashes.
