# Contributing to OpenPets

Thanks for helping improve OpenPets. This guide covers local setup, development workflow, tests, and pull request expectations.

## Requirements

- macOS 14 or later.
- Swift 6.0 or later.
- Xcode command line tools.

Check your Swift version:

```sh
swift --version
```

## Setup

From a local checkout, fetch dependencies through Swift Package Manager:

```sh
cd openpets
swift build
```

Run the tests before making changes:

```sh
swift test
```

Start the menu bar app during development:

```sh
swift run openpets-menubar
```

Run the CLI against a local pet bundle:

```sh
swift run openpets run --pet /path/to/starcorn
```

## Project Layout

- `Sources/OpenPetsCLI`: Command-line interface.
- `Sources/OpenPetsMenuBar`: Menu bar app and MCP HTTP server/tools.
- `Tests/OpenPetsTests`: Unit tests for the CLI, menu bar app, assistant setup, release packaging, and MCP tool metadata.

The embeddable runtime, IPC, animation, configuration, host UI, and bundled Starcorn pet live in the separate `OpenPetsKit` package at `https://github.com/alterhq/OpenPetsKit`.

## Development Workflow

1. Create a focused branch for your change.
2. Keep changes small and purpose-driven.
3. Add or update tests for behavior changes.
4. Update `README.md` or other docs when user-facing behavior changes.
5. Run `swift test` before opening a pull request.

Prefer minimal, direct changes over large rewrites. If a change affects public CLI commands, config keys, MCP tool names, or pet bundle format, call that out clearly in the pull request.

## Coding Guidelines

- Follow the style already present in the codebase.
- Prefer clear names and straightforward control flow.
- Keep APIs small unless there is a concrete reuse need.
- Avoid adding compatibility layers unless they protect persisted config, shipped CLI behavior, or external integrations.
- Add comments only when they explain non-obvious behavior or constraints.
- Use Swift concurrency and `@MainActor` consistently for AppKit/UI work.

## Tests

Run the full test suite:

```sh
swift test
```

Useful manual checks:

```sh
swift run openpets-menubar
swift run openpets ping
swift run openpets notify --title "Test" --status message --text "Hello from OpenPets"
swift run openpets animate waving --once
```

When changing MCP behavior, verify the relevant tool schema and descriptions in tests. When changing pet rendering or message layout, add tests for geometry helpers where possible.

## Pet Assets

Pet bundles must include a `pet.json` manifest and a spritesheet with an 8x9 atlas layout. Keep contributed assets original or clearly licensed for redistribution.

If you add or modify bundled assets, include provenance and licensing details in the pull request.

## Built-In Plugins

OpenPets plugin support is currently built-in and pull-request based. Do not create a separate plugin repository for new plugins until OpenPets ships an external plugin installer, runtime manifest, sandbox/permissions policy, and update UX.

Built-in plugin pull requests must:

- Emit semantic `surface.update` data and optional `pet.reaction` data only.
- Use host-owned cloud hotspots; do not add plugin-owned windows, web views, custom renderers, or absolute positioning.
- Keep long-running work off the main actor.
- Include tests for emitted surfaces, reactions, and missing-data behavior.
- Document user-facing behavior and any sensitive system, file, network, or API access.

The first-party battery plugin is the canonical reference for the current built-in plugin model.

## Security

OpenPets can expose an MCP server and open callback URLs from notification actions. Treat network binding, URL handling, and local IPC changes as security-sensitive.

Do not open public issues for vulnerabilities. Report security issues privately to the maintainers.

## Pull Requests

A good pull request includes:

- A short description of the user-facing change.
- Tests or a clear explanation of why tests were not added.
- Documentation updates for CLI, config, MCP tools, or pet bundle changes.
- Screenshots or a short screen recording for visible UI changes.
- Notes about security-sensitive behavior, migration concerns, or compatibility impacts.

Before requesting review, confirm:

- `swift test` passes.
- New files do not include secrets, local machine paths, or generated build output.
- `.build/`, `.swiftpm/`, Xcode user data, and other local artifacts are not committed.

## Reporting Bugs

Please include:

- macOS version.
- Swift version.
- How you installed or launched OpenPets.
- Steps to reproduce the issue.
- Expected and actual behavior.
- Relevant logs or terminal output.
- Whether the issue involves the menu bar app, CLI, MCP server, or pet rendering.

## Feature Requests

Please describe the problem you want solved, the proposed behavior, and whether it affects CLI commands, MCP tools, config, pet assets, or the desktop UI.
