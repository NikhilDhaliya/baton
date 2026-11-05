# Setting up baton (for Claude)

The user asked you to read this. Explain what the setup does, and if they say yes, walk them through it step by
step. Do every step you can yourself; tell them clearly when a step is theirs. Change nothing before their yes.

## 1. Look

    rm -rf ~/.cache/baton-setup && git clone -q --depth 1 https://github.com/NikhilDhaliya/baton ~/.cache/baton-setup
    python3 ~/.cache/baton-setup/baton scan

That's only a scratch copy in `~/.cache`. If the clone fails, stop and tell the user; never run a baton you didn't just clone. The scan only reads files on this
computer: it changes nothing and sends nothing. (Inside a clone of this repo already? Use `./baton` instead.)

## 2. Explain

Reply in a few short lines, fitted to the scan, then ask if they want it. For example (use the scan's numbers):

> With this setup:
> - **One Claude, many accounts.** `claude`, `claudeb`, `claudec`... share the same rules, memory, agents and
>   skills, in every folder. (You have 2 accounts and 5 memory notes; I'll bring those in.)
> - **Smooth handoff.** When an account nears its usage limit, Claude saves the work to HANDOFF.md and opens
>   another of your accounts in a new terminal, which carries on from there.
> - **Local + cloud (optional).** A private GitHub repo keeps the same Claude on your other computers and in
>   Claude Code cloud sessions, synced after every reply.
> - The status bar shows every account's usage.
>
> Nothing is deleted (your files are backed up) and it can be undone. Want it?

Add one plain line: moving work between accounts at the limit may count as getting around usage limits under
Anthropic's terms (see the README's Notes), so it's their call. Also mention, in a line each, what the scan says
limits a piece: no window opener (handoff prints the command to
paste instead), `claude` or `~/.local/bin` not on PATH (fixed in step F), a hook of theirs that already does part
of this (usage checks, memory sync: say baton would replace it).

## 3. If yes: two questions

Ask both at once (AskUserQuestion if you have it):
1. **How many Claude accounts?** Default: what the scan found; offer to add more (each is a separate Claude login).
2. **Local only, or local + cloud?** Local + cloud needs a private GitHub repo (step B).

## 4. Steps

Show the steps as a numbered list, then do them in order, saying "your turn" where it is.

**A. Install** (you)

    mkdir -p ~/.local/bin && cp ~/.cache/baton-setup/baton ~/.local/bin/baton && chmod +x ~/.local/bin/baton

Always call it as `~/.local/bin/baton` (it may not be on PATH yet).

**B. GitHub repo** (local + cloud only). Go down this list and take the first that fits:
1. They already have a baton repo (another computer set it up): ask for its URL. Then C with `--remote <url>`.
2. The scan says gh is signed in: you create it in C with `--github my-claude`.
3. gh installed, not signed in: their turn: type `! gh auth login`, pick GitHub.com and log in in the browser.
   Then option 2.
4. No gh: their turn:
   1. open https://github.com/new
   2. name it `my-claude`, choose **Private**, leave "Add a README" off, click **Create repository**
   3. copy the URL shown (HTTPS or SSH) and paste it here.

   Then C with `--remote <url>`. If the push then fails for want of a git login: easiest is installing gh and
   `! gh auth login` (it signs git in too); or use the repo's SSH URL if they have an SSH key on GitHub. Never ask
   them to paste a password or token into the chat.

**C. Shared brain** (you): one of

    ~/.local/bin/baton init --github my-claude    # B2/B3
    ~/.local/bin/baton init --remote <url>        # B1/B4
    ~/.local/bin/baton init                       # local only (add a repo later with --remote)

**D. Accounts** (you create, they sign in)
- Each extra account folder in the scan: `~/.local/bin/baton add b --dir ~/.claude-b` (name it after its letter
  or alias). Their own aliases keep working.
- Each new account: `~/.local/bin/baton add`, which makes the next free one: `claudeb`, then `claudec`...
- Their turn, for each new account: open a new terminal, run `claudeb` (or `~/.local/bin/claudeb`), type
  `/login`, and sign in with that account's email. Then `claudec`, and so on.

**E. Bring in memory and rules** (you)
- Read `~/.baton/CLAUDE.md`. If accounts had different rules, baton appended each set: merge them into one clean
  set, and ask where two rules conflict.
- If the scan showed memory notes (per-project, or another memory folder): write each lasting fact to
  `~/.baton/memory/` as one file, with one line in `~/.baton/memory/MEMORY.md`
  (`- [Title](file.md) — what it says`). Merge duplicates, name project notes after the project, keep MEMORY.md
  under 200 lines, leave the originals. Then `~/.local/bin/baton sync`.

**F. Check** (you): `~/.local/bin/baton doctor`. Fix any FIX line, or tell them exactly what to do:
- `~/.local/bin` not on PATH: give them the line for their shell profile (`export PATH="$HOME/.local/bin:$PATH"`).
- `claude` not on PATH: it's usually in `~/.local/bin` or `~/.claude/local`, which the account commands also
  check; if it isn't installed as a command at all: `curl -fsSL https://claude.ai/install.sh | bash`.

**G. Cloud** (local + cloud only, their turn): at https://claude.ai/code, start a session on the `my-claude` repo.
It loads the same rules and memory and saves what it learns back.

## 5. Done

One short message: what's set up; restart open Claude sessions; `claudeb`, `claudec`... start each account;
nothing else to do day to day (`baton status` shows usage); another computer: ask Claude there the same thing;
undo: `baton leave ~/.claude-b`. Then `rm -rf ~/.cache/baton-setup`.

## Rules

- Never print a token or `.credentials.json`. Don't copy secrets (keys, passwords, tokens) from their notes into
  the shared memory: it syncs to the repo. baton itself refuses to sync any file that looks like it holds a
  secret (`.env`, keys, tokens) and says which; tell the user.
- Change nothing before the user's yes. Never delete their files; baton moves them to `baton-backup/`.
- The accounts must be the user's own.
