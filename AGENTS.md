# Repository guidance

For SnowVision / endermend work, first read `teams/endermend/AGENTS.md` and
`teams/endermend/context/{START_HERE,CURRENT,DECISIONS,RUNBOOK}.md`.
Compare the local checkpoint HEAD with Git and check the index and working tree.
Use the working context alongside the checkpoint and preserve unrelated changes.
Never commit `.context-local`, secrets or runtime databases.

SnowVision gets the hourly forecast from Yandex Weather using the server-side
`X-Yandex-Weather-Key`. It fetches the Kamchatka section of the official
Roshydromet bulletin separately. Preserve source attribution and distinguish
expired or unverified warning dates from current bulletin entries. Snowfall
alerts and webhook delivery still depend on camera evidence.

Other teams retain their own applicable instructions and workflows.
