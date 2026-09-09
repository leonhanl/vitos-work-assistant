# Project Principles

## General
- Keep the implementation minimal and MVP-oriented.
- Prefer simple, explicit, and readable code over abstractions.
- Implement only what is required for the current task and MVP.
- Do not introduce new frameworks or dependencies unless clearly necessary.
- Avoid premature generalization and speculative extensibility.
- Do not add production-grade hardening unless it is required for core functionality or explicitly requested.

## Project Purpose
- This project serves both as a learning exercise and as a realistic demonstration of Palo Alto Networks capabilities, including Prisma AIRS.
- Build the application as a plausible real-world MVP, not as an intentionally insecure demo.
- Do not introduce artificial vulnerabilities, unreasonable design choices, or contrived behavior solely to demonstrate a security product.
- Do not proactively eliminate realistic shortcuts, incomplete controls, or imperfections unless they interfere with core functionality or an explicitly requested requirement.
- Prefer realistic MVP trade-offs over unnecessary hardening. Security issues demonstrated by the project should arise naturally from plausible architecture and implementation choices.

## Architecture
- Preserve the intended architectural semantics of the system.
- Do not simplify away important behaviors such as authentication, authorization, identity propagation, state ownership, or tool boundaries.
- Follow the intended usage patterns of the frameworks and libraries in use. Do not bypass them merely to reduce code size.
- Prefer the simplest correct implementation of the intended architecture.

## Development Principles
- Prefer straightforward, idiomatic Python.
- Avoid unnecessary design patterns, wrapper layers, factories, registries, or generic abstractions.
- Do not add infrastructure that is not required by the current MVP.
- Before adding an abstraction, verify that there are at least two concrete current use cases for it.
- Keep control flow explicit and easy to follow.
- Prefer local, task-specific changes over broad refactoring.
- Do not refactor unrelated code unless necessary to complete the requested change.

## Error Handling
- Handle errors that are important to the core workflow or necessary for diagnosing failures.
- Do not add extensive defensive programming, retries, fallbacks, or complex exception hierarchies unless specifically required.
- Do not silently suppress errors. Prefer clear failures that are easy to diagnose during development.

## Testing
- Run relevant existing tests after modifying code.
- Add tests when necessary to validate core behavior or an important architectural assumption.
- Focus testing on the main happy path and critical boundaries rather than exhaustive edge-case coverage.
- Do not optimize for test coverage percentage.
- Do not add extensive production-grade test infrastructure unless explicitly requested.
- Do not rewrite unrelated tests simply to make them pass.

## Scope Discipline
- Make the smallest change that correctly solves the requested problem.
- Do not implement adjacent features unless explicitly requested or strictly necessary.
- If a simpler implementation is sufficient for the MVP, prefer it over a more extensible design.