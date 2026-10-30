# US engine v1

`us_engine_v1.zip` is a deterministic source archive of the tracked Python package at
Git revision `2439a2586d433b9580c83775b0fbc34aa7bf483f`, under the private package name
`_stock_alarm_us_v1`. It contains source only, no credentials or account data.

SHA-256: `51ed7ce65e056e530a694a56e4851ab17ba5db21087a5c6a40f6d65d2fcbc1d7`.

All seven legacy `ENGINE_FILES` hashes in the existing US accounts match this revision.
`us_engine.py` verifies the archive before loading it. US preparation uses its regime
labeling and replay uses its original US strategy replay entry point. Domestic code
updates do not replace this engine. The archive's collection/server entry points are
not invoked; live input collection and orchestration remain in the current package.

The archived files can be inspected directly with any ZIP reader. A different strategy
requires a separately versioned archive and explicit verified configuration migration.
