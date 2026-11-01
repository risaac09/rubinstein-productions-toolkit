# Examples

Released under CC0. Every name, email address and number in these files is invented. The email addresses use the reserved example.com and example.org domains. Nothing here comes from a real practice.

- `practice/` is a small valid practice: three contacts, two projects and three financial entries (two invoices and an expense). Together they use every value of every enum in `skeleton/schemas/`. Run `skeleton/bin/validate clerkwork/examples/practice`.
- `negative/` is a ledger set with one defect: a project whose `contactId` points at a contact that does not exist. `validate` must exit 1 and name the id.

To read the Monday queue for the example practice as it stood on 2026-10-22:

```bash
CLERKWORK_ROOT=clerkwork/examples/practice CLERKWORK_TODAY=2026-10-22 clerkwork/skeleton/bin/today
```
