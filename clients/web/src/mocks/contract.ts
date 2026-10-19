// MSW handlers typed by the contract (iOS TDD §4.3): "MSW handlers type their
// responses from the schema so a mock cannot drift from the contract either."
//
// `contract.get("/api/v1/paths/{path_id}", resolver)` registers an ordinary MSW
// handler, with two things checked at compile time:
//   - the route exists in `clients/openapi.json` under that method (the key is
//     the contract's own path template, turned into MSW's `:param` form here);
//   - every response the resolver returns has a body the contract allows for
//     that operation: one of its JSON bodies (the error envelope included), an
//     empty body where it declares no content, or a stream where it declares
//     `text/event-stream`.
// The SSE frames written into a stream are typed by `sseFrame` below.

import {
  type DefaultBodyType,
  HttpResponse,
  type HttpResponseResolver,
  type PathParams,
  http,
} from "msw";
import type { Schemas } from "../lib/api";
import type { paths } from "../lib/api/schema";

type Method = "get" | "post" | "put" | "patch" | "delete";

/** The contract paths that declare `method`. */
export type ContractPath<M extends Method> = {
  [P in keyof paths]: [NonNullable<paths[P][M]>] extends [never] ? never : P;
}[keyof paths];

type Responses<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]> extends {
  responses: infer R;
}
  ? R
  : never;

/** One declared response's body, as MSW sees it. */
type BodyOf<R> = R extends { content: { "application/json": infer B } }
  ? B
  : R extends { content: { "text/event-stream": unknown } }
    ? ReadableStream
    : null;

/** Every body the contract allows `method path` to answer with. */
export type ContractResponse<P extends keyof paths, M extends Method> = Extract<
  { [S in keyof Responses<P, M>]: BodyOf<Responses<P, M>[S]> }[keyof Responses<P, M>],
  DefaultBodyType
>;

/** `/api/v1/paths/{path_id}` -> `/api/v1/paths/:path_id` (MSW's param syntax). */
export function toMswPath(path: string): string {
  return path.replace(/\{([^}]+)\}/g, ":$1");
}

function route<M extends Method>(method: M) {
  return <P extends ContractPath<M>>(
    path: P,
    resolver: HttpResponseResolver<PathParams, DefaultBodyType, ContractResponse<P, M>>,
  ) => http[method]<PathParams, DefaultBodyType, ContractResponse<P, M>>(toMswPath(path), resolver);
}

/** Contract-typed `http.*`: the route key is the contract's path template. */
export const contract = {
  get: route("get"),
  post: route("post"),
  put: route("put"),
  patch: route("patch"),
  delete: route("delete"),
};

/** A `204`: what the contract declares as no content. */
export function noContent(): HttpResponse<null> {
  return new HttpResponse(null, { status: 204 });
}

/** The shared error envelope (`ErrorEnvelope`), as the backend renders it. */
export function errorEnvelope(
  status: number,
  code: string,
  message: string,
  { details, requestId = "test-request-id" }: { details?: unknown; requestId?: string } = {},
): HttpResponse<Schemas["ErrorEnvelope"]> {
  return HttpResponse.json(
    {
      error: {
        code,
        message,
        request_id: requestId,
        ...(details === undefined ? {} : { details }),
      },
    },
    { status },
  );
}

/** Each SSE event name and its frame's `data` schema (`dtos/stream.py`). */
export interface StreamFrames {
  delta: Schemas["StreamDelta"];
  tutor_check: Schemas["StreamTutorCheck"];
  proposal: Schemas["StreamProposal"];
  done: Schemas["StreamDone"];
  error: Schemas["StreamError"];
}

/** One SSE frame on this wire (docs/api.md): a named event, single-line JSON `data`. */
export function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** One SSE frame, its `data` typed by the event's contract schema. */
export function sseFrame<E extends keyof StreamFrames>(event: E, data: StreamFrames[E]): string {
  return frame(event, data);
}
