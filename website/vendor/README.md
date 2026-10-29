# Local SVGR dependency cleanup

This private unpacked package preserves the published `@svgr/plugin-svgo` 8.1.0
executable, types, source map, README and MIT license bytes. Its metadata removes
the unused `deepmerge` dependency, omits the original publication's `gitHead`, and
sets `private: true`. Version 8.1.0 identifies its upstream base. The local `file:`
directory and per-file SHA-256 hashes in `svgr-plugin-svgo-provenance.json`
identify this unreleased local package; it is not an upstream fixed release.

The directory is intentionally unpacked: the unchanged profile archive guard
forbids `.tgz` and `.tar.gz` files in source and build contexts. No archive
exemption, alternate archive extension, scanner filter or age exemption is used.
The former archive ignore exception is removed entirely.
The MIT license is both alongside this document and inside the package.
The existing application Docker context exclusion for the website stays in place.

To reproduce all six files, obtain the authentic public archive from
`https://registry.npmjs.org/@svgr/plugin-svgo/-/plugin-svgo-8.1.0.tgz`
outside the checkout, then choose a new external output directory. Replace the
two uppercase placeholders with those external paths:

```sh
python rebuild-svgr-plugin-svgo.py ORIGINAL_ARCHIVE_PATH NEW_EXTERNAL_DIRECTORY
```

The script verifies the original SHA-512 integrity and every resulting file hash
before writing. It makes no network request, executes no package code, and
refuses to overwrite an existing output directory. Compare its output with
`svgr-plugin-svgo/`; do not store the input archive in the checkout.

The root development dependency establishes the directory identity. Its `$`
override selects the same local package for the transitive webpack consumer.
The website's `install-links=true` setting makes normal `npm ci` pack this local
directory into an ordinary installed package instead of creating a symlink.
That preserves the consumer in npm's audit input. The committed lock records its
local directory and accurate 8.1.0 metadata. Prefer a genuine upstream cleanup release when available; regenerate
the lock and verify real consumer resolution, unchanged unrelated dependencies,
and continued absence of deepmerge before removing this vendor.
