# CineRecast — rules for coding agents

CineRecast recreates a reference video shot for shot while replacing chosen people and backgrounds. It is a Python command-line tool plus an agent skill. The tool does the deterministic work (analysis, planning files, the approval ledger, conforming, similarity scores, assembly); an agent follows the skill to run paid generation jobs and to judge results by eye.

Read these before changing anything:

- `docs/2026-11-18-cine-recast-design.md` — what the tool must do and must never do (Korean).
- `docs/2026-11-18-stage1-core-plan.md` — the task-by-task implementation plan for stage 1 (Korean).

## Hard rules

1. No Chinese anywhere: source, comments, strings, model prompts, docs, file names, commit messages. A test fails if any Han character is found in the repository. When a test needs one, write it as an escape such as `\u4e2d`.
2. Code, comments, data keys, error messages and model prompts are English. Skill documents and generated reports are Korean.
3. Do not copy code from other projects into this repository.
4. The tool never handles API keys and makes no network calls. Paid generation is run by the agent, and only for jobs the tool has issued after a recorded approval.
5. Takes are never overwritten. The deliverable never contains frames or audio copied from the reference video.
6. This repository is public. Never commit keys, personal paths, people's photos or working video. Project folders live outside the repository; tests generate their own synthetic clips with ffmpeg.
7. Run external programs with argument lists, never shell strings.

## Working here

- Python 3.12, Windows 11, PowerShell 7, ffmpeg 8.1 on PATH.
- Set up: `pwsh -NoProfile -File install.ps1` (creates `~/.cine-recast/venv`, installs this folder in editable mode, registers the skill).
- Tests: `& "$HOME\.cine-recast\venv\Scripts\python.exe" -m pytest tests -q` from the repository root.
- Write the failing test first, then the code.
- Commit only the files you changed, by path. Never `git add -A` or `git commit -a`.
- Commit messages start with `feat:`, `fix:`, `docs:` or `infra:`.
