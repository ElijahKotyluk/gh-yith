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
gh yith plan                          # writes the plan and prints its path
$EDITOR PLAN                          # set class and reply per thread
gh yith post PLAN                     # dry run: live check, prints an approval code
gh yith post PLAN --approve CODE      # send exactly that plan
gh yith show PLAN                     # the plan file as written, offline; after posting, with results
```

`plan` writes into the repository's git directory, or the worktree's own git
directory in a worktree, so the plan is never committed. `-o FILE` puts it
somewhere else. Each entry gets a class and a reply:

| class | reply | what `post` does |
| --- | --- | --- |
| FIX | required | reply, then resolve as addressed |
| WONT_FIX | text | reply, then resolve as won't fix |
| WONT_FIX | empty | resolve as won't fix, no reply |
| INVALID | text or empty | same as WONT_FIX, resolved as invalid |
| HUMAN | empty | nothing |
| HUMAN | text | reply as a question, leave open |

`"resolve": false`, a JSON boolean, means reply only. Delete an entry to
leave that thread alone; the dry run lists every open thread the plan leaves
out.

## Why a plan file

What the CLI enforces, whoever or whatever drives it:

- **Only what was approved is sent.** `post` without `--approve` is a dry run.
  It checks the plan against the live PR and prints exactly what it would send
  now, using each live thread's own path, line and comment, then an approval
  code. The code is a hash of that exact send: every reply and resolution,
  which threads, and the PR's head commit. `post --approve CODE` checks again
  and sends only if the result still matches. Any edit that changes what
  would be sent, a thread resolved or reopened since, or a new push changes
  the code, so the send is refused and nothing goes out.
- **All or nothing up front.** A malformed entry, an entry whose id and path
  point at different threads, a thread that is not on the PR, or one you lack
  permission for rejects the whole plan before anything is sent.
- **No double posts from a plan file.** A reply or resolution this plan file
  already sent is never sent again, even if the plan is edited. Each write is
  recorded before it is sent, so after an interrupted send the next dry run
  checks whether it reached GitHub. A thread resolved on GitHub in the
  meantime is left alone.
- **One send per PR at a time.** A lock per PR for your user, kept in
  `~/.cache/yith/locks` and taken before the plan is read, stops two sends to
  the same PR from overlapping, whichever plan file or copy they use. A send that waited for the lock re-checks live
  state, so a copy approved alongside the original is refused once the
  original has posted.
- **A record.** What was sent is written into the plan after each step,
  including when a send fails or is stopped with Ctrl-C, SIGTERM or SIGHUP.
- **No hidden text.** Replies may only contain letters, digits, punctuation,
  symbols, ordinary spaces, newlines and accents; zero-width, text-direction,
  variation-selector and other invisible characters are refused. Third-party
  text shown in the terminal, including file paths, escapes exactly the
  characters a reply would refuse, so whatever it shows plainly is visible.

What it cannot enforce:

- The approval code is not a secret: whoever runs the dry run sees it, agent
  included. It proves nothing changed since that output; it cannot prove you
  read it. The skill tells the agent to show you the dry run and wait.
- A process killed with SIGKILL leaves its lock behind and may not record the
  step it was on. A reply that is already the latest on its thread is still
  skipped.
- Two machines, or two different home directories, sending to the same PR at
  once are not coordinated.
- The single `reply` and `resolve` commands post without a plan at all.

When an AI agent fills the plan, a review comment that tries to talk the agent
into posting something through the plan gets no further than the dry run,
which shows the text next to the live comment it answers. That does not stop
a manipulated agent from editing code, using `reply` or `resolve` directly, or
approving its own dry run; the skill's rules and your own review cover those.

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

`list -f json` output carries a schema number, currently 1, and plan files
carry their own, currently 2. A change that breaks either one bumps that
number and the minor version.

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
