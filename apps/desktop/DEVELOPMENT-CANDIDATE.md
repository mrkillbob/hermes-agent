# macOS development candidates

A development candidate is an explicit, integrity-checked build for review. It
uses ad-hoc signatures, not Developer ID or notarization trust. It is not an
install-ready release and does not authorize installation, Gatekeeper changes,
or a live gateway handoff.

On macOS arm64, the maintained build entry point accepts:

```sh
python -S -B scripts/bundles/desktop.py --commit <full-fork-SHA> \
  --variant bundled --development-candidate --work <fresh-job> --cache <owned-inputs>
```

The mode is part of the immutable prepared request and its digest. It accepts
only a bundled, native darwin-arm64 commit build; tag, channel, Store and release
selectors are refused. Ambient signing credentials, keychain selectors,
notarization inputs and signature caches are refused rather than ignored. Normal
release preparation/signing remains unchanged.

The candidate-only configuration leaves the normal certificate signer unused
and invokes the existing shared signing policy in the final afterSign phase,
after framework and Electron fuse mutations. It uses literal ad-hoc identity,
timestamp-none, no keychain, no provisioning-profile discovery and no signature
cache. Nested Chromium and wheel ZIP handling remains in place. Final payload
facts are refreshed before the outer app seal.

This hook placement depends on the locked electron-builder lifecycle. The native
regression builds a disposable minimal Electron directory, proves a real fuse
change precedes the candidate callback, and trips on final-phase identity
discovery. Its signer is inert; that fixture is never exported as a candidate.

The actual candidate build must pass export verification on the app extracted
from the retained ZIP: strict/deep code signature, explicit ad-hoc mode, hardened
runtime, arm64 executable, matching app/payload commit stamps, source-matching PM
lock and every tool's locked artifact identity and final-byte digest. The receipt
records package, payload, bundled source snapshot, stamp, manifest and lock
hashes. Gatekeeper assessment is recorded separately; a rejection is not bypassed.
No candidate is retained when verification fails.

The fork-only `Fork Desktop development candidate` workflow qualifies the native
contracts on pull requests with standard hosted macOS runners. After the route
is reviewed and qualified, its manual main-only job can build an accepted full
fork commit that is an ancestor of main. It has contents-read permissions, no
release environment or signing secrets, no feed/release publication, and one-day
candidate artifact retention. Select a commit whose exact-head source,
security/provenance and required review gates have passed; main ancestry alone
is not a test result.

The artifact receipt explicitly says `installationAuthorized: false`. Review
the exact artifact hashes/signature limitations and the installed gateway owner
before requesting any separate installation/rollback/handoff action.
