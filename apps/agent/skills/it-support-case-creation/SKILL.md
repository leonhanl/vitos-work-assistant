---
name: it-support-case-creation
description: Use this skill when IT troubleshooting has not resolved an employee's problem and they want a support case or Jira ticket. Prepare a complete, accurate case for human review before creation. Do not use it while the user still wants troubleshooting only.
---

# IT support case creation

Turn the conversation and completed troubleshooting into a case that an IT agent can
act on without making the employee repeat information.

## Establish the request shape

Use `jira_get_request_types` with the configured service desk, select the request type
that best matches the problem, then use `jira_get_request_type_fields` to learn its
required fields. Do not invent a request type or field value. Ask only for required or
high-value information that is missing from the conversation.

Do not search Jira for duplicate cases. A related knowledge-base article is not proof
that the employee's request is a duplicate.

## Prepare a useful case

Write a short, searchable summary containing the affected service, the main symptom,
and an exact error code when one is known.

Organize the description around the facts available for this incident:

- Problem and actual behavior
- Business impact and affected scope
- Device, operating system, client, network, or location when relevant
- Start time and last-known-working time when known
- Exact error messages or codes
- Troubleshooting already completed and the result of each step
- Expected behavior

Reuse facts already supplied by the employee or returned by tools. Clearly distinguish
confirmed facts from assumptions, and omit sections whose values are unknown rather
than inventing them. Never include passwords, access tokens, MFA codes, recovery codes,
or other secrets. Do not add the employee's identity to the description merely to set
the requester; the application supplies the trusted requester separately.

## Review and create

Call `jira_create_customer_request` only after the case has a usable summary,
description, request type, and every required field. The application will pause this
write operation and present the exact case for explicit approval. Do not interpret
ordinary conversational agreement as bypassing that approval.

If the user denies the action, acknowledge the decision and do not claim that a case
was created. If the tool succeeds, report the returned request or issue key. If the
result is missing or uncertain, say that creation could not be confirmed and do not
retry the create operation automatically.
