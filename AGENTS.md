# SnowVision repository guidance

This repository contains SnowVision in `teams/endermend/snowvision`.
Read `teams/endermend/AGENTS.md` and `teams/endermend/context/START_HERE.md`,
then follow their task-specific links. Communicate in Russian.

Keep context focused on current behavior, active requirements and verification.
Remove completed checklists, obsolete handoffs and duplicate instructions;
Git history and ignored local checkpoints retain past work. Preserve technical
source attribution, current product limitations and user runtime data.

Complete authorized work through an appropriate check, commit and push to
`fork/endermend`, including documentation and cleanup. Pushes for this work go
only to `https://github.com/endermend/algorithms-kamchatka` on the `endermend`
branch. Do not push to `origin` or create/update PRs in another repository.
Create a PR only when the user explicitly requests one.
Preserve unrelated edits unless the user explicitly asks to include them.
Never commit `.env`, credentials, databases, `.quality-local`, `.context-local`
or raw conversation logs. Never use force push or `docker compose down -v`.
