# Bugfix Prompt

```text
A verification gate failed. Do not proceed to new features. Read the failure output, identify the narrowest failing contract, inspect related tests and source files, and fix the smallest cause. Do not weaken tests unless the test contradicts a higher-authority spec; if it does, document the conflict in WORKLOG.md and update the spec/test together.

After the fix, rerun the exact failed command, then run python scripts\verify.py. Append the result to WORKLOG.md.
```
