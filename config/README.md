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

# config/headrush_topologies.json - origin

Generated, not vendored, and NOT a device read. The ten HeadRush signal-path
templates: per routing, which of the fourteen slots are common, which sit on a
parallel branch, and which belong to an independent path.

The unit publishes the ten names on `Chain.Routing` and nothing about their
shapes. Verified on hardware (#109): writing each of the ten in turn and
reading the whole chain object back leaves every per-slot property
byte-identical. So the shapes are read out of the vendor's own web editor,
which the unit serves, by `tools/build_headrush_topologies.py`. That is the
best available source and it is still not the API, which is why the file
carries `provenance: "vendor editor bundle"` and `api_readable: false`, and why
`devices/headrush/topology.py` carries both onto every `Topology` it hands out.

The dual-path partitions are measured off the editor's own slot geometry rather
than read out of the routing names, because the names do not always state one:
`Dual Path 4-10` does and `Dual Straight Path` does not. Each routing records
which axis carried the split in `partition_method`, and where a name does state
a partition the generator cross-checks it and refuses if the two disagree.

Do not hand-edit. Regenerate with `tools/build_headrush_topologies.py`, from a
unit or with `--from-file` against a saved bundle. The generator refuses rather
than shipping something short: fewer than ten definitions, a routing with other
than fourteen slots, or names that disagree with the committed schema's own
`Routing` enumeration all exit non-zero and write nothing.

See THIRD_PARTY_NOTICES.md for provenance and trademarks.

# config/headrush_registry.json - origin

Generated from `config/headrush_schema.json` by
`tools/build_headrush_registry.py`, which reads no hardware and no network. The
schema is the device describing itself; this is that description rearranged
into the questions a caller asks. Every block by object path, its parameters
classified into the kinds a caller must treat differently, and the `ModuleType`
ordinal that selects a block into a chain slot.

CONTINUOUS VALUES ARE NORMALISED 0..1 ON THE WIRE, while `display_minimum`,
`display_maximum` and the published `format` describe the scale the unit SHOWS.
Measured on a Core by writing a value and reading the unit's own screen:
`Amp.Bass` at wire 0.75 reads 75 % on a 0..100 range, `Amp.PostGain` at 0.5
reads 0.0 dB on -12..12. The HTTP API cannot settle this on its own, because
the device accepts a write of either 0.75 or 75 and clamps neither. The
generalisation from one block to all 302 objects rests on the schema rather
than on those readings: 3912 continuous parameters publish a default, all 3912
lie in 0..1, and 1369 lie outside their own published display range, so they
cannot be display values at all.

NO NORMALISED-TO-DISPLAY CONVERSION IS OFFERED. The device publishes an opaque
curve id per parameter (`x-options.normalizeAlgo`, carried as `taper_id`) and
never says what an id denotes. Measured: `Amp.Bass` carries no id and is
linear, `Amp.TremSpeed` carries id 5 and is quadratic. Unit is not a proxy
either, since `C2_Bass_Chorus.Depth` is a percentage carrying id 6.
`Parameter.to_display()` exists only to refuse. Completing the survey is #126.

Every field the device published travels verbatim in each parameter's
`published` map, including ones nothing in this repo interprets, and a test
proves that set complete against the schema. An earlier draft kept only the
fields it had a use for and consequently told callers the taper was
unpublished while `normalizeAlgo` sat in the schema it was built from.

Owner state is excluded by object path and the reasons are in
`excluded_objects`: rig library, setlists, the save dialog, file access, the
cloud sessions, and `Patch/Rig`, which carries the loaded preset name.

Do not hand-edit. Regenerate with `tools/build_headrush_registry.py`, and
`--check` exits non-zero if the committed file is not what the generator
produces. `devices/headrush/registry.py` refuses to serve answers derived from
a schema the repo no longer holds, distinguishing a firmware bump from a
hand-edited schema.

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
