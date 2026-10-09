# Fork patches (mrkillbob/hermes-agent)

Edits this fork carries that upstream (`NousResearch/hermes-agent`) does not have. Every
upstream sync must keep them. After merging `upstream/main`, run the guard tests below; a
failure means the sync dropped or reverted a patch — re-apply it, do not delete the test.
Resolve sync conflicts by hand (never `--theirs` a whole file listed here).

| Patch | Files | Guard test |
|---|---|---|
| Skill env passthrough is keyed per conversation session, not process-wide (sibling chats cannot inherit a skill's secret names) | `tools/env_passthrough.py` | `tests/tools/test_env_passthrough_fork_isolation.py` |
| Media proxy enforces its 25 MB cap while streaming | `hermes_cli/web_routers/files.py` | `tests/hermes_cli/test_media_proxy_streaming_limit.py` |
| Transcription split lists only `partNNN.m4a` segments | `tools/transcription_chunking.py` | `tests/tools/test_transcription_chunking_fork_names.py` |
| Upload limit uses the `pre_transcription` hook's model | `tools/transcription_tools.py` | `tests/tools/test_transcription_hook_model_limit.py` |
| `transcribe-stream` cancels its session on non-object/malformed first frame | `hermes_cli/web_routers/audio.py` | `tests/hermes_cli/test_transcribe_stream_cleanup.py` |
| Docs-site `npm audit` gate allow-lists advisories with no upstream fix (`braces`, GHSA-vfj7-8cjw-p6xm); everything else high/critical still fails | `.github/workflows/docs-site-checks.yml`, `scripts/ci/npm_audit_gate.py` | `tests/scripts/test_npm_audit_gate.py` |
| xAI streaming defers end-of-audio until `transcript.created` instead of dropping queued PCM | `tools/transcription_streaming.py` | `tests/tools/test_xai_streaming_early_eos.py` |
| Desktop dictation falls back to the recorded blob when the startup PCM buffer overflows | `apps/desktop/src/app/chat/composer/hooks/use-voice-recorder.ts` | `use-voice-recorder-warmup.test.tsx` ("startup PCM buffer overflows") |

```bash
scripts/run_tests.sh tests/tools/test_env_passthrough_fork_isolation.py \
  tests/hermes_cli/test_media_proxy_streaming_limit.py tests/tools/test_transcription_chunking_fork_names.py \
  tests/tools/test_transcription_hook_model_limit.py tests/hermes_cli/test_transcribe_stream_cleanup.py tests/scripts/test_npm_audit_gate.py \
  tests/tools/test_xai_streaming_early_eos.py
# plus: (cd apps/desktop && npx vitest run src/app/chat/composer/hooks/use-voice-recorder-warmup.test.tsx)
```

Add a row (and a guard test in a fork-only file upstream never edits) for every new patch.
