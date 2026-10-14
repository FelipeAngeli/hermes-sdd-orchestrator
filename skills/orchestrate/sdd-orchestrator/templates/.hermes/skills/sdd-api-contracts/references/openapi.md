# OpenAPI descriptions

Load when a slice authors, changes, validates or reviews an OpenAPI description, or compares one with a server or client. The rest of this skill (source hierarchy, contract surfaces, evidence limits) still applies; this file adds what is specific to OpenAPI.

## OpenAPI and Swagger

OpenAPI is the specification, maintained by the OpenAPI Initiative. Swagger was the specification's original name up to Swagger 2.0, which was donated to the OpenAPI Initiative; today "Swagger" names a family of tools (for example Swagger UI, Swagger Editor, Swagger Codegen). Say "OpenAPI description" for the document and name a Swagger tool only when the project uses it. A file called `swagger.json` may hold any version; read its version field.

## Detect the version

- The root field decides: `swagger: "2.0"` is OpenAPI/Swagger 2.0; `openapi: 3.0.x`, `3.1.x` or `3.2.x` is OpenAPI 3.
- Read the specification for that exact minor line at `https://spec.openapis.org/oas/`; rules differ between lines. Examples:
  - 2.0 uses `definitions`, `parameters` with `in: body` and `securityDefinitions`; 3.x uses `components` and `requestBody`.
  - 3.0 Schema Objects are an extended subset of an older JSON Schema draft and express null with `nullable: true`.
  - 3.1 Schema Objects are a superset of JSON Schema Draft 2020-12: `nullable` is gone and null is a type (`type: ["string", "null"]`); `exclusiveMinimum`/`exclusiveMaximum` are numbers, not booleans.
- Record whether the description is generated from code (annotations, framework plugin) or hand-written, and which file is the source. Edit the source, never generated output.
- Do not upgrade the version as a side effect; a version change is its own decision, checked against every tool and generator that consumes the file.

## Authoring and review checklist

- Paths and operations: path templates match server routes; every operation has a unique, stable `operationId` when generators or clients rely on it.
- Parameters: location (`path`, `query`, `header`, `cookie`), `required`, type, format, style/explode for arrays and objects; path parameters are always required.
- Request bodies and schemas: media types the server accepts; reusable schemas under `components` referenced with `$ref`.
- Required and nullability: `required` lists properties that must be present; nullability says whether `null` is allowed. They are independent; a field can be required and nullable, or optional and non-nullable.
- Enums: every value the server can emit; state how clients treat unknown values. Adding an enum value can break clients that decode strictly.
- Responses: every status code the server returns, including errors, with a schema for the error envelope; do not document only `200`.
- Authentication: `securitySchemes` in components (`securityDefinitions` in 2.0) and `security` at root or operation level, matching the server's real enforcement, including public endpoints that override it.
- Examples: valid against their schema and taken from real or authoritative payloads; never containing real credentials or personal data.
- Pagination: parameters, limits, ordering and the response metadata or links the server actually returns.

## Validate structure and references

- Run the validator or linter the project already configures (its script, CI step or ruleset). If none exists, report the gap; adding a tool is a dependency decision.
- Validation must resolve every `$ref`, including external files, and fail on unresolved or circular references the tooling cannot handle.
- A valid document is not a correct contract; validation proves structure, not agreement with the server.

## Breaking changes and consumers

Treat as breaking for existing consumers unless the project's versioning policy says otherwise: removing or renaming a path, operation, parameter, property or enum value; making an optional request field required; making a response field optional or nullable; narrowing a type or format; changing authentication; changing status codes or the error envelope. Name the consumers (generated clients, mobile apps, partners) and the version or rollout window that carries the change. Use the project's diff/breaking-change tool when configured.

## Compare with server, serializers and clients

- Compare the description with server routes, validation rules and the serializer's real output: naming policy (casing), omitted versus `null` fields, date/time and decimal encoding, enum serialization.
- Compare generated or hand-written clients with the same description; a regenerated client is evidence only after it compiles and its tests use real or authoritative payloads.
- Report each divergence with the field, path and source as in `evidence-and-limits.md`.

## Limits

- Never invent a path, field, status code, enum value, nullability or security requirement to make the description complete; record a gap.
- Never call a production API to discover its contract without explicit authorization; prefer a local server, test environment or the served description from an authorized environment.
