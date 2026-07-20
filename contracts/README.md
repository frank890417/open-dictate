# Machine-readable contracts

This directory is the stable boundary between the public Open Dictate core and
optional private product overlays. It contains no personal glossary, recording,
transcript, speaker profile, token, absolute local path, or private adapter.

- `protocol.v1.json` describes the daemon transport, command set, and error set.
- `protocol.schema.json` is its portable JSON Schema.
- `daemon-message.schema.json` and `daemon-response.schema.json` describe JSONL payloads.
- `product-config.schema.json` defines the branding and runtime values an overlay may supply.
- `product-config.open-dictate.json` is the public product configuration.
- `overlay-lock.schema.json` defines how an overlay pins an exact upstream release.

Run `python3 scripts/check-contracts.py`. A private overlay can additionally run:

```sh
python3 scripts/check-contracts.py \
  --product-config /path/to/private/product-config.json \
  --overlay-lock /path/to/private/open-dictate.lock.json \
  --source-archive /path/to/open-dictate-source.tar.gz
```

The source archive is optional during development but mandatory in the release
upgrade procedure. Its SHA-256 must match the lock file. An overlay config may
point at an external lexicon through an environment variable name; it must never
embed the external path or private data in this public repository.

Contract changes follow these rules:

`wireProtocolVersion` means the compatibility version of bytes exchanged over
the Unix socket. It is deliberately separate from an integration document's
title revision (for example `IO-CONTRACT v1.3`) and from feature notes such as
`add_pair v1.4`. Those editorial labels do not make an overlay incompatible.

1. Additive optional fields may retain the current wire protocol major version.
2. Removing or changing a command, required field, response meaning, framing, or
   security boundary requires a protocol major-version bump.
3. Public CI validates the public configuration. Each overlay validates its own
   configuration and lock file against the checked-out public contracts.
4. Generated artifacts consume these files; they do not redefine the contract.
