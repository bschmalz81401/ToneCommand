# config/fm9_catalog.json - origin

`fm9_catalog.json` is copied verbatim from the mcp-midi-control project's
`packages/fractal-midi/catalog/fm9.json`:

- https://github.com/TheAndrewStaker/mcp-midi-control
- Copyright 2026 Stephen Staker
- Apache License 2.0

It is the FM9 device-true parameter catalog (2,052 parameters with display
ranges and typecodes, plus amp/drive/reverb rosters), mined from FM9-Edit
and hardware-validated by that project. The roster tables inside derive in
part from fractal-syx-codec (Apache-2.0, Copyright 2026 Andrew Mercurio).

See THIRD_PARTY_NOTICES.md at the repository root for the reproduced
NOTICE. If Fractal firmware updates renumber parameters, refresh this file
from the upstream catalog rather than hand-editing it.

# config/amp_models.json - origin

Generated, not vendored. Maps each `FM9_AMP_ROSTER` ordinal to the
real-world amp it models, so the planner can match "Plexi era" or "a
JCM800" to Fractal's oblique naming. Built by `tools/build_amp_models.py`
from the community Amplifier Library Guide: the modeled amp, original cab,
DynaCab pairing, controls, tubes, and tonestack position. Only the `model`
field reaches the planner prompt today; the rest is stored for future use.

The guide's prose notes and tips are its author's writing and are not carried
here. `--with-prose` extracts them for local use to
`config/amp_models.full.json`, which is gitignored so that build cannot
overwrite this one. See THIRD_PARTY_NOTICES.md for provenance.

Do not hand-edit: `fm9/registry.py` checks at load time that every record's
`fractal` field still equals `FM9_AMP_ROSTER[ordinal]` and raises
`AmpModelsStale` if a catalog refresh renumbered the roster. Corrections and
gap-fills belong in the generator's `OVERRIDES` table, then regenerate.

# config/headrush_tapers.json - origin

Generated, not vendored, and NOT a device read. The eleven normalisation curves
that convert between the 0..1 value a HeadRush takes on the wire and the value
it shows on screen.

The unit publishes an opaque integer per continuous parameter
(`x-options.normalizeAlgo`) and no formula, which is why a registry built from
the schema alone cannot convert: a caller cannot evaluate a name. The vendor's
web editor has to convert, so it carries the curves, and
`tools/build_headrush_tapers.py` reads them out of the bundle the unit serves.
That is the best available source and it is still not the API, which is why the
file carries `provenance: "vendor editor bundle"` and `api_readable: false`.

NOTHING IS TRANSCRIBED BY EYE. A hand-copied formula is a guess that looks like
a fact, and `Db` and `AllenHeathFaderVolume` are exactly the shapes that survive
a typo while returning plausible numbers. The generator extracts the vendor's
own functions, RUNS them under node over a grid of 726 points, and commits the
results. `devices/headrush/tapers.py` is checked against those vectors rather
than against anyone's reading of the JavaScript.

Six readings taken off a Core's screen are reproduced exactly by the committed
formulas, with the formulas derived first. The generator refuses to write
anything if they are not, so a bundle whose maths disagrees with the hardware
fails loudly. That is corroboration on one block and not proof across 302
objects, and the `warning` field says so.

`normalizeAlgo` 3 (`DelayRatio`) is in the vendor's enum and in neither lookup
table, so its own dispatch falls back to Linear. Recorded as `unimplemented`
rather than filed under Linear, because an id with no implementation and an id
implemented as linear are different facts about the firmware. An id OUTSIDE the
enum raises instead, because a firmware publishing a curve this table has never
seen must not be quietly scaled as linear.

The vendor's source text is not committed, only sha256 prefixes of the
fragments read. See THIRD_PARTY_NOTICES.md.

Do not hand-edit. Regenerate with `tools/build_headrush_tapers.py --from-file`
against a saved bundle; `--check` exits non-zero if the committed file is not
what that bundle produces. node is needed to REGENERATE, because the vendor's
code is executed rather than retyped, and is not needed to use the result.

# config/headrush_amp_models.json - origin

Generated, not vendored. A SECOND device's roster: maps each HeadRush
amp-model ordinal to the real amplifier the manufacturer says it emulates,
across the two amp blocks (`Amp` and `ReValver Amp`, 53 and 48 ordinals).
Landed ahead of any HeadRush adapter on purpose (#33 phase 5): grounding data
needs no adapter, no device handle and none of the contract work in #109.

Built by `tools/build_headrush_amp_models.py` from two artifacts that are
themselves generated, in the sibling `HeadrushRigBuilder` project: the
vendor's published attribution list, scraped from its product page, and the
DEVICE's own self-description fetched over its HTTP API, which is what
supplies the ordinals. Neither is transcribed by hand and neither is vendored
here. See THIRD_PARTY_NOTICES.md for provenance and trademarks.

Unlike the FM9 sidecars there is no load-time drift guard, because this repo
vendors no HeadRush catalog to compare against. Every row carries `headrush`,
the device's own Model option at that ordinal, which is what a guard would
check once an adapter can read `Amp.Type` back from a unit. The reason is
recorded in `fm9.grounding.UNGUARDABLE` rather than left to be rediscovered.

Do not hand-edit. `OVERRIDES` in the generator is a SPELLING table for the
join key only and cannot correct an attribution: the attributions are the
vendor's own words, and rewriting one would stop this being a record of what
they published. An ordinal the vendor does not describe is stored as
`model: null` with a reason, and the generator refuses to build when an
ordinal has neither an attribution nor a declared absence.

There is deliberately no cross-map to the FM9 roster. Both name real
amplifiers, so a pivot looks like string matching; measured, exact matching
finds 4 of 100 and fuzzy matching maps a Vox AC30 onto an AC15. That needs a
human-confirmed mapping and is its own piece of work.

# config/cab_models.json - origin

Generated, not vendored. Maps the FM9's stock cabs to the real cabinets they
were captured from.

    cabs.0    FACTORY 1   1023/1024 mapped
    cabs.1    FACTORY 2   1023/1024 mapped
    cabs.3    LEGACY        189/189 mapped
    dynacabs                  45/45 mapped, keyed by name

IR-bank records are keyed by bank id then slot ordinal
(`FM9_CAB_ROSTERS_BY_BANK` / `FM9_CAB_BANK_NAMES`) and carry `model` plus the
cab name broken into `size`, `fractal_name`, `mic` and `variant`
("1x6 Dan-O 121" -> 1x6 / Dan-O / 121). Legacy records add `brand` and the
Fractal alias for that manufacturer ("GERMAN BOUTIQUE" = Bogner). DynaCabs are
a cab mode rather than IR slots, so they have no roster ordinal and are keyed
by name.

Facts only: `model` is the cabinet's identity reduced to its first clause. The
source's commentary and quotations are not reproduced. `--with-prose` keeps the
full text in `config/cab_models.full.json`, which is gitignored.

Built by `tools/build_cab_models.py` from a saved copy of the Fractal wiki's
"Cab models" page - the wiki sits behind a Cloudflare challenge that refuses
scripted fetches, so the page has to be saved from a browser first.

Two details worth knowing before changing the generator:

- For factory banks the description is **not in the table**. It is the
  "Based on ..." paragraph introducing each table, one table per cabinet.
  The table's Creator column is who made the IR, not what the cab is, and is
  dropped.
- The size/name/mic split is derived from the page's own grouping rather than
  from a microphone vocabulary: every row in a table is the same cabinet, so
  the tokens they all share are the name and the first that varies is the mic.

Corrections go in the generator's `CAB_OVERRIDES` / `DYNACAB_OVERRIDES`, not in
the generated JSON. The join is by slot (the wiki numbers each bank from 1, the
catalog from 0), with names compared only to confirm the offset still holds;
the build aborts if too few agree. `fm9/registry.py` raises `CabModelsStale` if
a record's `fractal` no longer matches the catalog roster.
