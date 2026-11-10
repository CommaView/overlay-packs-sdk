# Contributing

Issues and pull requests are welcome: a bug in the checker, a pack the checker reads differently from the app, a
doc that's wrong or unclear, a better example.

## How changes land

This repository is exported from CommaView's private source, which stays the source of truth: the checker, the pack
host and the examples are the same code the comma and CommaView's own tests use, and each sync here replaces this
repository's copy of them.

So a pull request isn't merged here directly. A maintainer reviews it, ports an accepted change into CommaView's
source (crediting you), and it comes back in the next sync, when the pull request is closed with a link to it. Changes
made only here would be overwritten by the next sync.

## Before you open a pull request

- Run the checker on every example: `tools/build-examples.sh` (it exits 1 if any pack fails).
- For a change to the checker, say which pack, overlay or manifest shows the problem, and what the app or the comma
  does with it.
- Keep a change to one topic.

## Your own packs

You don't need a pull request to share a pack: submit its `.cvpack` at my.commaview.com › Packs (see
[docs/extensions.md](docs/extensions.md#sharing-a-pack-the-catalog)). A pull request that adds a pack to `examples/`
is for one that shows something no example does.

## Security

Please don't open a public issue for a security problem (a pack that escapes the limits the comma holds it to, say):
report it privately from this repository's Security tab (Report a vulnerability) instead.
