# Contract surfaces to compare

- Field identity: name, casing convention, and whether a rename left the old name live for existing clients.
- Type: numeric width and precision, string versus number for identifiers and money, date and time encoding, timezone assumptions.
- Nullability and required status: a field the server may omit but the client declares non-null, and a field the client sends as null where the server demands a value; optional with a default is not the same as nullable.
- Enum: a value the server can emit that the client cannot decode, a client value the server rejects, casing and wire-format differences, and whether the client fails closed on an unknown value.
- Collections: an empty collection versus a null collection, and item nullability inside them.
- Endpoint lifecycle: obsolete or removed routes still called by the client, new routes never adopted, changed method or path, version prefixes.
- Request shape: path, query and body parameters, headers, authentication scheme and idempotency keys.
- Response shape: status codes the client handles versus those the server emits, the error envelope and its field-level error structure, and pagination metadata.
- Semantics types cannot express: the unit of a numeric field such as cents versus a decimal amount, identifier scope and ordering guarantees.

Classify each divergence by blast radius — silent data loss, runtime deserialization failure, wrong value reaching a business rule, or dead code — and by urgency: one that breaks today versus one that breaks on the next server change.
