# Public release checklist

APMA's public portfolio release must contain only product code, synthetic test
fixtures, public documentation, and explicitly public screenshots/evidence.

## Release route used for this repository

This repository was created from one reviewed, clean-history snapshot. That
separation keeps historical development branches and runtime work outside the
public boundary.

Before publishing any later snapshot:

1. Export only the reviewed commit tree; do not copy `.git`, `jobs/`, `.env`,
   provider credentials, unrelated workspaces, or ignored audio.
2. Confirm that the only audio permitted is explicitly synthetic or licensed
   test material.
3. Scan the exported files and the new repository's complete history for API
   keys, private-key blocks, personal paths, non-public project identifiers,
   meeting names, family names, addresses, and transcript text.
4. Run the complete Docker dry-run suite with all live-provider gates disabled.
5. Inspect every screenshot and evidence file visually before pushing.
6. Publish only the MIT-licensed snapshot after owner review.

The source development history remains separate. A source-control merge does
not automatically authorize a later public release.
