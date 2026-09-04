# HRL-6 Opportunity Scoring Engine Design

## Scope

Every opportunity uses one complete schema. Each observed or unavailable field is backed by raw,
source-referenced evidence. Scoring uses named ordinal bands rather than synthetic decimal
precision. No score can cite an evidence ID outside the candidate or a source field unrelated to
that score.

## Scoring and ranking

The eight required score dimensions are directional attractiveness bands. The ranking formula uses
separate ordinal factors for expected value, automation, recurrence, defensibility, human labor,
capital required, and platform risk. It applies the specified multiplicative/divisive emphasis
internally, publishes only a coarse A-E tier, and sorts exact integer fractions without exposing a
misleading pseudo-precise score.

An unavailable raw field stays unavailable. It is never silently assigned a neutral or zero value.
Assessment construction requires explicit evidence-backed bands and fails closed on missing,
duplicate, invalid, or cross-domain evidence references.
