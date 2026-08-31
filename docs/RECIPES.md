# Tone Recipes

A recipe is HOW to build a tone, not the tone file itself: a named,
cited, replayable list of ToneCommand actions. You share the knowledge;
everyone replays it on their own unit against their own base preset.

## Finding and sharing them

**Reading needs no account.** Recipes live in this repository's `recipes/`
folder, which is public, so the app fetches them straight out of it and
browses them in place. Nobody signs in, nobody clicks through a web UI.

**Sharing has two paths**, because the two halves of this are not equally
hard and pretending one route fits everyone is what went wrong first. SHARE
RECIPE saves the file into your own `recipes/` folder and puts it on the
clipboard, so you can pass it on however you already talk to people. If you
do use GitHub, it will also open a prefilled new file in `recipes/`, where
one button turns it into a proposal.

It used to open a prefilled GitHub *issue*. That was wrong three ways at
once: an issue is not a container for a recipe, the tracker would silt up
with them, and it asked a guitarist to learn a developer's tool before they
could give anything back.

## Why recipes and not preset files

- Preset files carry other people's paid work; recipes carry facts and
  decisions, with citations. Nothing paid is ever redistributed.
- Recipes are readable: every step says what it does and why.
- Recipes are portable: they name blocks, parameters, and model types by
  their grounded names, and validation runs against YOUR device's schema
  before a single byte is sent.

## Format (v1)

```json
{
  "recipe_version": 1,
  "name": "goodbye-yesterday-rock-intro",
  "title": "Goodbye Yesterday: dry crunch intro + produced body",
  "device": "FM9",
  "author": "monzta1",
  "tested_firmware": "11.00",
  "sources": [
    "E Edwards (Elevation Rhythm) official tutorial: heavy OD throughout,
     small room reverb, two-delay staging"
  ],
  "assumes": "an 8-scene preset with amp, drive, delay and reverb blocks",
  "actions": [ ...planner-vocabulary actions... ],
  "ear_checklist": ["soft picking sings, never chokes", "..."]
}
```

Actions use exactly the planner's vocabulary (set_scene, set_param,
set_bypass, set_channel, set_type, set_tempo, add_block, bind_pedal,
rename_scene). `store` is FORBIDDEN in recipes: replay is edit-buffer
only, and storing stays a human decision at the console.

## Replaying

    python tools/replay_recipe.py recipes/name.json            # dry-run: validate only
    python tools/replay_recipe.py recipes/name.json --apply    # edit buffer
    TONECOMMAND_SIM=1 ... --apply                              # simulator

Every action is validated against the connected device's schema first;
the run stops at the first failure; the ear checklist prints at the end
because read-backs are not proof of tone.
