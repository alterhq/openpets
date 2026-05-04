# Agent Guidelines

Commits should use conventional commits.

Examples:

```text
feat: add pet adoption search
fix: handle empty pet profile response
docs: add agent contribution guidelines
style: format Swift files with swift-format
refactor: split pet loading logic into PetService
test: add adoption flow unit tests
chore: update Swift package dependencies
build: require Swift 6 toolchain
ci: run swift test on pull requests
perf: cache rendered pet thumbnails
feat(auth): add Sign in with Apple
fix(api): retry transient network failures
feat(api)!: replace legacy pet response model
```

This project uses Swift 6.

## Coding Style

We use Swift 6.x:

- All code should be modern Swift 6 friendly.
- Respect separation of concerns. Prefer dedicated classes for business logic, UI, and other responsibilities.
- Create new files when needed. We prefer more small files over a few large files.
- Respect the DRY principle and avoid code duplication.
- Use actors when appropriate.
- Avoid `Timer`, DispatchQueue-based concurrency models, and `NSLock`. Prefer async/await, `Task`, `ContinuousClock`, and actors.

### Required Standards

- Delete old code when replacing it.
- Use meaningful names: `userID`, not `id`.
- Use early returns to reduce nesting.
- Leave no warnings after compilation unless you can justify them.

## Performance & Security

### Measure First

- Do not optimize prematurely.
- Benchmark before claiming something is faster.

## Communication Protocol

### Progress Updates

```text
✓ Implemented authentication (all tests passing)
✓ Added rate limiting
✗ Found issue with token expiration - investigating
```

### Suggesting Improvements

```text
The current approach works, but I notice [observation].
Would you like me to [specific improvement]?
```

### Reviewing

After you are done implementing:

1. Build the project to check for compilation errors.
2. Start a subagent to review your changes and ensure the initial issues are addressed.
