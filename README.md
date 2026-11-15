# gh-yith

A GitHub CLI extension that triages pull request review threads and posts
the result in one checked batch. It works on its own, by hand or from any
script or agent.

Nothing in it is specific to a project, a language, or an agent.

## Install

```bash
gh extension install ElijahKotyluk/gh-yith
gh extension upgrade yith    # later
```

Requires `gh` signed in (`gh auth status`) and `python3` 3.8 or newer.
macOS and Linux; Windows is untested.

To have an AI agent run the whole review/fix cycle, use the
[yith skill](https://github.com/ElijahKotyluk/yith). It carries its own copy
of this CLI, so neither needs the other.

## The plan workflow

```bash
gh yith plan                                   # writes .git/yith/plan-<PR>.json
$EDITOR .git/yith/plan-1670.json               # set class and reply per thread
gh yith show .git/yith/plan-1670.json          # exactly what will be posted
gh yith post .git/yith/plan-1670.json          # dry run, sends nothing
gh yith post .git/yith/plan-1670.json --yes    # send
```

The plan lives inside `.git`, so it is never committed. Each entry gets a
class and a reply:

| class | reply | what `post` does |
| --- | --- | --- |
| FIX | required | reply, then resolve as addressed |
| WONT_FIX | text | reply, then resolve as won't fix |
| WONT_FIX | empty | resolve as won't fix, no reply |
| INVALID | text or empty | same as WONT_FIX, resolved as invalid |
| HUMAN | empty | nothing |
| HUMAN | text | reply as a question, leave open |

`"resolve": false` on an entry means reply only. Delete an entry to leave
that thread alone.

## Why a plan file

Every write to GitHub goes through `post`, which sends only what the plan
says:

- **You see the exact text first.** `show` prints the plan the way it will be posted.
- **Dry run by default.** `post` without `--yes` checks every entry against the live PR and sends nothing.
- **All or nothing up front.** A malformed entry, a thread that is not on the PR, or one you cannot resolve rejects the whole plan before anything is sent.
- **No double posts.** A reply already on the thread and a thread already resolved are skipped, so re-running after a failure is safe.
- **A record.** Each entry's result is written back into the plan file.

When an AI agent fills the plan, a review comment that tries to talk the
agent into posting something can at worst get text into the plan. You read
the plan before anything is sent.

## Single operations

```bash
gh yith list -f table                       # PR for the current branch
gh yith list 1670 --all-authors -f md       # humans too, as markdown
gh yith list --fail-if-open                 # exit 2 while bot threads stay open
gh yith reply PRRT_... -m "Fixed." --resolve -n   # dry run a single reply
gh yith resolve PRRT_... --reason invalid
gh yith rerequest                           # ask Copilot to review again
```

Every write takes `-n` / `--dry-run`. A PR can be given as a number, a URL,
or left out for the current branch's PR. By default only threads started by
bot accounts are listed: Copilot's reviewer, CodeRabbit, GitHub Actions, any
GitHub App. `--author REGEX` filters on the login instead, `--all-authors`
shows everyone.

## Compatibility

`list -f json` output and plan files carry a schema number, currently 1. A
change that breaks either one bumps that number and the minor version.

This repo is the source of truth for the CLI. The yith skill bundles
byte-identical copies of `gh-yith` and `tests/test_yith.py`; after a change
here, run `scripts/sync-cli` in the yith repo to copy them over.

## Development

```bash
python3 -m unittest discover -s tests
gh extension install .    # from a checkout named gh-yith: run your working copy as gh yith
```

No dependencies. Tests mock the `gh` boundary and never reach GitHub.

## License

MIT
