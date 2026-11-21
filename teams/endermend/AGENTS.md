# Endermend workflow

Before work, read context/CURRENT.md, context/DECISIONS.md and context/RUNBOOK.md.
Keep these handoff files current after material decisions and verification.
Use the evidence-driven loop in RUNBOOK. Never invent successful checks.
Use tools/endermend-context/context.py record for material decisions/check outcomes.
The local post-commit hook captures an ignored checkpoint. Do not recursively commit it.
Never publish .context-local, credentials, runtime databases or raw conversation logs.
