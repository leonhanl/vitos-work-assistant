# Project Principles

## General
- Keep the implementation minimal and MVP-oriented.
- Prefer simple, explicit code over abstractions.
- Do not introduce new frameworks or dependencies unless clearly necessary.
- Avoid premature generalization.

## Development Principles
- Prefer straightforward Python.
- Avoid unnecessary design patterns.
- Do not add infrastructure that is not required by the current MVP.
- Before adding an abstraction, verify that there are at least two concrete use cases for it.

## Testing
- Run relevant tests after modifying code.
- Do not rewrite unrelated tests simply to make them pass.